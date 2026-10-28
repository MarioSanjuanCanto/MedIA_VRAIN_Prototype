import assert from 'node:assert/strict';
import test from 'node:test';
import { SpeechPauseDetector } from './voice-activity.ts';

test('a pause after speech submits once enough speech has been detected', () => {
  const vad = new SpeechPauseDetector(0);
  for (let t = 50; t <= 500; t += 50) assert.equal(vad.observe(.05, t), 'continue');
  assert.equal(vad.observe(0, 1500), 'continue');
  assert.equal(vad.observe(0, 1900), 'pause');
});
test('silence does not send an empty request', () => {
  const vad = new SpeechPauseDetector(0);
  assert.equal(vad.observe(0, 1400), 'continue');
  assert.equal(vad.observe(0, 10000), 'empty');
});
test('brief pauses within a sentence do not end the turn', () => {
  const vad = new SpeechPauseDetector(0);
  for (let t = 50; t <= 500; t += 50) vad.observe(.05, t);
  assert.equal(vad.observe(0, 1300), 'continue');
  vad.observe(.05, 1400);
  assert.equal(vad.observe(0, 2500), 'continue');
  assert.equal(vad.observe(0, 2800), 'pause');
});
test('a brief click in otherwise silent input is not a spoken request', () => {
  const vad = new SpeechPauseDetector(0);
  vad.observe(.2, 50);
  assert.equal(vad.observe(0, 1800), 'continue');
  assert.equal(vad.observe(0, 10000), 'empty');
});
