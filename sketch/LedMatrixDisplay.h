#pragma once

#include <Arduino.h>
#include <array>
#include <cstdint>
#include <vector>

class LedMatrixDisplay {
public:
  static constexpr int FRAME_STORAGE_WORDS = 5;

  void begin();
  void update();

  void draw(const std::vector<uint8_t>& frame);
  bool loadFrame(const std::array<uint32_t, FRAME_STORAGE_WORDS>& data);
  void playAnimation();
  void stopAnimation();
  void writeSentence(const String& text);
};
