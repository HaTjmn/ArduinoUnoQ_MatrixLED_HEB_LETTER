// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
// SPDX-License-Identifier: MPL-2.0

// Displays individual frames, buffered animations, and scrolling Hebrew text
// on the Arduino LED matrix. Each helper function performs one small operation.

#include "LedMatrixDisplay.h"
#include <Arduino_LED_Matrix.h>
#include <array>
#include <string.h>
#include <vector>
#include <zephyr/kernel.h>
#include "Animation.h"

Arduino_LED_Matrix matrix;
K_MUTEX_DEFINE(anim_mtx);

// -----------------------------------------------------------------------------
// Display configuration
// -----------------------------------------------------------------------------

constexpr int MATRIX_ROWS = 8;
constexpr int MATRIX_COLS = 13;
constexpr int MATRIX_PIXELS = MATRIX_ROWS * MATRIX_COLS;
constexpr uint8_t PIXEL_OFF = 0;
constexpr uint8_t PIXEL_ON = 7;

// -----------------------------------------------------------------------------
// Buffered animation configuration and state
// -----------------------------------------------------------------------------

constexpr int MAX_FRAMES = 300;
constexpr int FRAME_DATA_WORDS = 4;
constexpr int FRAME_STORAGE_WORDS = LedMatrixDisplay::FRAME_STORAGE_WORDS;
constexpr int FRAME_DURATION_INDEX = 4;

static uint32_t animationBuffer[MAX_FRAMES][FRAME_STORAGE_WORDS];
static int animationFrameCount = 0;
static int currentAnimationFrame = 0;
static bool animationIsRunning = false;
static unsigned long nextAnimationTime = 0;

// -----------------------------------------------------------------------------
// Hebrew alphabet configuration
// -----------------------------------------------------------------------------

constexpr uint8_t HEBREW_UTF8_LEAD_BYTE = 0xD7;
constexpr uint8_t HEBREW_FIRST_CODE_BYTE = 0x90;
constexpr uint8_t HEBREW_LAST_CODE_BYTE = 0xAA;

constexpr size_t ANIMATION_FRAME_COUNT =
    sizeof(animation) / sizeof(animation[0]);

struct LetterBounds {
  int firstColumn;
  int lastColumn;
};

// -----------------------------------------------------------------------------
// Scrolling-text configuration and state
// -----------------------------------------------------------------------------

constexpr int SCROLL_MAX_COLUMNS = 800;
constexpr int LETTER_GAP_COLUMNS = 1;
constexpr int WORD_GAP_COLUMNS = 4;
constexpr uint32_t SCROLL_STEP_MS = 120;

static uint8_t scrollBuffer[MATRIX_ROWS][SCROLL_MAX_COLUMNS];
static int scrollContentWidth = 0;
static int scrollWindowStart = 0;
static bool scrollIsActive = false;
static bool scrollLoopEnabled = false;
static unsigned long nextScrollTime = 0;

// -----------------------------------------------------------------------------
// General state helpers
// -----------------------------------------------------------------------------

static bool hasTimeArrived(unsigned long now, unsigned long scheduledTime) {
  return static_cast<long>(now - scheduledTime) >= 0;
}

static void stopBufferedAnimation() {
  animationIsRunning = false;
  animationFrameCount = 0;
  currentAnimationFrame = 0;
}

static void stopScrollingText() {
  scrollIsActive = false;
}

static void clearScrollBuffer() {
  memset(scrollBuffer, PIXEL_OFF, sizeof(scrollBuffer));
  scrollContentWidth = 0;
  scrollWindowStart = 0;
}

static void resetAllPlayback() {
  stopBufferedAnimation();
  stopScrollingText();
}

// -----------------------------------------------------------------------------
// Hebrew character helpers
// -----------------------------------------------------------------------------

static bool isHebrewLetter(const uint8_t* character) {
  return character[0] == HEBREW_UTF8_LEAD_BYTE &&
         character[1] >= HEBREW_FIRST_CODE_BYTE &&
         character[1] <= HEBREW_LAST_CODE_BYTE;
}

static bool isSpace(const uint8_t* character) {
  return character[0] == ' ';
}

static int getHebrewLetterIndex(const uint8_t* character) {
  return character[1] - HEBREW_FIRST_CODE_BYTE;
}

static int getLetterFrameIndex(const uint8_t* character) {
  int letterIndex = getHebrewLetterIndex(character);
  return hebrewLetterToFrame[letterIndex];
}

static bool isValidFrameIndex(int frameIndex) {
  return frameIndex >= 0 &&
         static_cast<size_t>(frameIndex) < ANIMATION_FRAME_COUNT;
}

static int getUtf8CharacterLength(const uint8_t* character) {
  return isHebrewLetter(character) ? 2 : 1;
}

// -----------------------------------------------------------------------------
// Letter-frame helpers
// -----------------------------------------------------------------------------

static bool getLetterPixel(int frameIndex, int row, int column) {
  const uint32_t* frameWords = animation[frameIndex];
  int pixelIndex = row * MATRIX_COLS + column;
  int wordIndex = pixelIndex / 32;
  int bitIndex = 31 - (pixelIndex % 32);
  return ((frameWords[wordIndex] >> bitIndex) & 1U) != 0;
}

static LetterBounds findLetterBounds(int frameIndex) {
  LetterBounds bounds = {MATRIX_COLS, -1};

  for (int row = 0; row < MATRIX_ROWS; ++row) {
    for (int column = 0; column < MATRIX_COLS; ++column) {
      if (!getLetterPixel(frameIndex, row, column)) continue;

      if (column < bounds.firstColumn) bounds.firstColumn = column;
      if (column > bounds.lastColumn) bounds.lastColumn = column;
    }
  }

  return bounds;
}

static bool hasVisiblePixels(const LetterBounds& bounds) {
  return bounds.lastColumn >= bounds.firstColumn;
}

static int getLetterWidth(const LetterBounds& bounds) {
  return bounds.lastColumn - bounds.firstColumn + 1;
}

static void copyLetterColumn(int frameIndex, int sourceColumn,
                             int destinationColumn) {
  if (destinationColumn < 0 || destinationColumn >= SCROLL_MAX_COLUMNS) return;

  for (int row = 0; row < MATRIX_ROWS; ++row) {
    scrollBuffer[row][destinationColumn] =
        getLetterPixel(frameIndex, row, sourceColumn) ? PIXEL_ON : PIXEL_OFF;
  }
}

static void copyVisibleLetter(int frameIndex, const LetterBounds& bounds,
                              int destinationStartColumn) {
  for (int sourceColumn = bounds.firstColumn;
       sourceColumn <= bounds.lastColumn; ++sourceColumn) {
    int offset = sourceColumn - bounds.firstColumn;
    copyLetterColumn(frameIndex, sourceColumn,
                     destinationStartColumn + offset);
  }
}

// -----------------------------------------------------------------------------
// Sentence layout helpers
// -----------------------------------------------------------------------------

static void addLetterWidth(int letterWidth, bool& previousWasLetter,
                           int& totalWidth) {
  if (previousWasLetter) totalWidth += LETTER_GAP_COLUMNS;
  totalWidth += letterWidth;
  previousWasLetter = true;
}

static void addWordGap(bool& previousWasLetter, int& totalWidth) {
  if (!previousWasLetter) return;

  totalWidth += WORD_GAP_COLUMNS;
  previousWasLetter = false;
}

static bool tryGetVisibleLetter(const uint8_t* character, int& frameIndex,
                                LetterBounds& bounds) {
  if (!isHebrewLetter(character)) return false;

  frameIndex = getLetterFrameIndex(character);
  if (!isValidFrameIndex(frameIndex)) return false;

  bounds = findLetterBounds(frameIndex);
  return hasVisiblePixels(bounds);
}

static int calculateSentenceWidth(const String& text) {
  int totalWidth = 0;
  bool previousWasLetter = false;
  const uint8_t* character =
      reinterpret_cast<const uint8_t*>(text.c_str());

  while (*character && totalWidth < SCROLL_MAX_COLUMNS) {
    int frameIndex;
    LetterBounds bounds;

    if (tryGetVisibleLetter(character, frameIndex, bounds)) {
      addLetterWidth(getLetterWidth(bounds), previousWasLetter, totalWidth);
    } else if (isSpace(character)) {
      addWordGap(previousWasLetter, totalWidth);
    }

    character += getUtf8CharacterLength(character);
  }

  return totalWidth > SCROLL_MAX_COLUMNS ? SCROLL_MAX_COLUMNS : totalWidth;
}

static void placeLetterFromRight(int frameIndex, const LetterBounds& bounds,
                                 int sentenceWidth, int& usedWidth,
                                 bool& previousWasLetter) {
  if (previousWasLetter) usedWidth += LETTER_GAP_COLUMNS;

  int letterWidth = getLetterWidth(bounds);
  int destinationStart = sentenceWidth - usedWidth - letterWidth;
  copyVisibleLetter(frameIndex, bounds, destinationStart);

  usedWidth += letterWidth;
  previousWasLetter = true;
}

static void placeSentenceInScrollBuffer(const String& text,
                                        int sentenceWidth) {
  int usedWidth = 0;
  bool previousWasLetter = false;
  const uint8_t* character =
      reinterpret_cast<const uint8_t*>(text.c_str());

  while (*character && usedWidth < sentenceWidth) {
    int frameIndex;
    LetterBounds bounds;

    if (tryGetVisibleLetter(character, frameIndex, bounds)) {
      placeLetterFromRight(frameIndex, bounds, sentenceWidth, usedWidth,
                           previousWasLetter);
    } else if (isSpace(character)) {
      addWordGap(previousWasLetter, usedWidth);
    }

    character += getUtf8CharacterLength(character);
  }
}

static void startScrolling(int contentWidth) {
  scrollContentWidth = contentWidth;
  if (scrollContentWidth <= 0) return;

  scrollWindowStart = scrollContentWidth;
  scrollIsActive = true;
  nextScrollTime = millis();
}

// -----------------------------------------------------------------------------
// Public display-layer API
// -----------------------------------------------------------------------------

void LedMatrixDisplay::draw(const std::vector<uint8_t>& frame) {
  if (frame.empty()) return;

  k_mutex_lock(&anim_mtx, K_FOREVER);
  matrix.draw(frame.data());
  k_mutex_unlock(&anim_mtx);
}

bool LedMatrixDisplay::loadFrame(
    const std::array<uint32_t, FRAME_STORAGE_WORDS>& data) {
  k_mutex_lock(&anim_mtx, K_FOREVER);

  if (animationFrameCount >= MAX_FRAMES) {
    k_mutex_unlock(&anim_mtx);
    return false;
  }

  int destinationFrame = animationFrameCount++;
  for (int word = 0; word < FRAME_STORAGE_WORDS; ++word) {
    animationBuffer[destinationFrame][word] = data[word];
  }

  k_mutex_unlock(&anim_mtx);
  return true;
}

void LedMatrixDisplay::playAnimation() {
  k_mutex_lock(&anim_mtx, K_FOREVER);
  currentAnimationFrame = 0;
  animationIsRunning = animationFrameCount > 0;
  nextAnimationTime = millis();
  k_mutex_unlock(&anim_mtx);
}

void LedMatrixDisplay::stopAnimation() {
  k_mutex_lock(&anim_mtx, K_FOREVER);
  resetAllPlayback();
  k_mutex_unlock(&anim_mtx);
}

void LedMatrixDisplay::writeSentence(const String& text) {
  k_mutex_lock(&anim_mtx, K_FOREVER);

  resetAllPlayback();
  clearScrollBuffer();

  int sentenceWidth = calculateSentenceWidth(text);
  placeSentenceInScrollBuffer(text, sentenceWidth);
  startScrolling(sentenceWidth);

  k_mutex_unlock(&anim_mtx);
}

void LedMatrixDisplay::setScrollLoop(bool loop) {
  k_mutex_lock(&anim_mtx, K_FOREVER);
  scrollLoopEnabled = loop;
  k_mutex_unlock(&anim_mtx);
}

// -----------------------------------------------------------------------------
// Buffered animation engine
// -----------------------------------------------------------------------------

static void buildMatrixFrame(int sourceFrameIndex,
                             uint32_t destination[FRAME_DATA_WORDS]) {
  for (int word = 0; word < FRAME_DATA_WORDS; ++word) {
    destination[word] = reverse(animationBuffer[sourceFrameIndex][word]);
  }
}

static uint32_t getFrameDuration(int frameIndex) {
  uint32_t duration = animationBuffer[frameIndex][FRAME_DURATION_INDEX];
  return duration == 0 ? 1 : duration;
}

static void advanceAnimationFrame() {
  ++currentAnimationFrame;
  if (currentAnimationFrame >= animationFrameCount) {
    stopBufferedAnimation();
  }
}

static void animationTick() {
  k_mutex_lock(&anim_mtx, K_FOREVER);

  unsigned long now = millis();
  if (!animationIsRunning || !hasTimeArrived(now, nextAnimationTime)) {
    k_mutex_unlock(&anim_mtx);
    return;
  }

  int frameIndex = currentAnimationFrame;
  uint32_t matrixFrame[FRAME_DATA_WORDS];
  buildMatrixFrame(frameIndex, matrixFrame);

  nextAnimationTime = now + getFrameDuration(frameIndex);
  advanceAnimationFrame();
  matrixWrite(matrixFrame);

  k_mutex_unlock(&anim_mtx);
}

// -----------------------------------------------------------------------------
// Scrolling-text engine
// -----------------------------------------------------------------------------

static uint8_t getScrollPixel(int row, int matrixColumn) {
  int sourceColumn = scrollWindowStart + matrixColumn;
  if (sourceColumn < 0 || sourceColumn >= scrollContentWidth) return PIXEL_OFF;
  return scrollBuffer[row][sourceColumn];
}

static void buildScrollFrame(uint8_t frame[MATRIX_PIXELS]) {
  for (int row = 0; row < MATRIX_ROWS; ++row) {
    for (int column = 0; column < MATRIX_COLS; ++column) {
      frame[row * MATRIX_COLS + column] = getScrollPixel(row, column);
    }
  }
}

static bool hasScrollFinished() {
  return scrollWindowStart < -MATRIX_COLS;
}

static void finishScrolling() {
  if (scrollLoopEnabled) {
    // Restart from fully off-screen, same as the initial startScrolling() state.
    scrollWindowStart = scrollContentWidth;
    return;
  }
  stopScrollingText();
  matrix.clear();
}

static void scrollTick() {
  k_mutex_lock(&anim_mtx, K_FOREVER);

  unsigned long now = millis();
  if (!scrollIsActive || !hasTimeArrived(now, nextScrollTime)) {
    k_mutex_unlock(&anim_mtx);
    return;
  }

  uint8_t frame[MATRIX_PIXELS];
  buildScrollFrame(frame);
  matrix.draw(frame);

  --scrollWindowStart;
  nextScrollTime = now + SCROLL_STEP_MS;

  if (hasScrollFinished()) finishScrolling();

  k_mutex_unlock(&anim_mtx);
}

// -----------------------------------------------------------------------------
// Arduino entry points
// -----------------------------------------------------------------------------

void LedMatrixDisplay::begin() {
  matrix.begin();
  matrix.setGrayscaleBits(3);
  matrix.clear();
}

void LedMatrixDisplay::update() {
  animationTick();
  scrollTick();
}
