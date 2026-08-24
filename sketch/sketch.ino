#include <Arduino_RouterBridge.h>
#include "LedMatrixDisplay.h"

LedMatrixDisplay display;

// Bridge providers are deliberately kept in the platform layer.
void draw(std::vector<uint8_t> frame) {
  display.draw(frame);
}

void load_frame(std::array<uint32_t, LedMatrixDisplay::FRAME_STORAGE_WORDS> data) {
  display.loadFrame(data);
}

void play_animation() {
  display.playAnimation();
}

void stop_animation() {
  display.stopAnimation();
}

void write_sentence(String text) {
  display.writeSentence(text);
}

void set_scroll_loop(bool loop) {
  display.setScrollLoop(loop);
}

static void registerBridgeProviders() {
  Bridge.provide("draw", draw);
  Bridge.provide("load_frame", load_frame);
  Bridge.provide("play_animation", play_animation);
  Bridge.provide("stop_animation", stop_animation);
  Bridge.provide("write_sentence", write_sentence);
  Bridge.provide("set_scroll_loop", set_scroll_loop);
}

void setup() {
  display.begin();
  Bridge.begin();
  registerBridgeProviders();
}

void loop() {
  display.update();
}
