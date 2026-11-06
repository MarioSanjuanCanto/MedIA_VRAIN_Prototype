import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { buildChatRequest } from './chat-requests.ts';

const commands = JSON.parse(readFileSync(new URL('./chat-commands.json', import.meta.url), 'utf8'));

test('clip instructions are sent separately from the original request', () => {
  const request = buildChatRequest('/clip TV05 Mieres', commands);
  assert.equal(request.message, '/clip TV05 Mieres');
  assert.ok(request.command_prompt.endsWith('TV05 Mieres'));
  assert.ok(!request.message.includes('media_speakers'));
});

test('clip and dubbing commands preserve the synchronized dubbing request', () => {
  const clip = buildChatRequest('/clip TV05 accidente de Mieres y dóblalo al inglés', commands);
  assert.equal(clip.message, '/clip TV05 accidente de Mieres y dóblalo al inglés');
  assert.match(clip.command_prompt, /media_dub_clip/);
  assert.match(clip.command_prompt, /accidente de Mieres y dóblalo al inglés$/);

  const dub = buildChatRequest('/doblar TV05 accidente de Mieres al inglés', commands);
  assert.equal(dub.message, '/doblar TV05 accidente de Mieres al inglés');
  assert.match(dub.command_prompt, /dubbed_video_file/);
});

test('subtitle command defaults to the original language', () => {
  const request = buildChatRequest('/subtitular TV05 accidente de Mieres', commands);
  assert.equal(request.message, '/subtitular TV05 accidente de Mieres');
  assert.match(request.command_prompt, /target_language=original/);
  assert.match(request.command_prompt, /media_subtitle_video/);
});

test('summary preserves its command so routing can distinguish it from search', () => {
  const request = buildChatRequest('/resumir TV05', commands);
  assert.equal(request.message, '/resumir TV05');
  assert.ok(request.command_prompt.endsWith('TV05'));
});

test('ordinary conversation stays unchanged', () => {
  assert.deepEqual(buildChatRequest('hola', commands), { message: 'hola' });
});

test('retry recovers a command from an older expanded user message', () => {
  const old = commands['/clip'].prompt.replace('{{input}}', 'TV05 Mieres');
  assert.equal(buildChatRequest(old, commands).message, '/clip TV05 Mieres');
});

test('exhaustive visual search keeps its API command for named videos', () => {
  assert.deepEqual(buildChatRequest('/clips-todos APUNT cartel', commands), {
    message: '/clips-todos APUNT cartel',
  });
});
