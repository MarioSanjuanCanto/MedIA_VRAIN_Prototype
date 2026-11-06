import assert from 'node:assert/strict';
import test from 'node:test';

import { insertIntoDraft } from './chat-draft.ts';

test('inserts a generated clip name at the cursor', () => {
  assert.deepEqual(
    insertIntoDraft('Subtitula  en inglés', 'TV05_clip.mp4', 10, 10),
    { draft: 'Subtitula TV05_clip.mp4 en inglés', cursor: 23 },
  );
});

test('appends a generated clip name with a separating space', () => {
  assert.deepEqual(
    insertIntoDraft('Dobla este clip:', 'TV05_clip.mp4'),
    { draft: 'Dobla este clip: TV05_clip.mp4', cursor: 30 },
  );
});

test('replaces the selected text with the generated clip name', () => {
  assert.deepEqual(
    insertIntoDraft('Usa este archivo', 'TV05_clip.mp4', 4, 8),
    { draft: 'Usa TV05_clip.mp4 archivo', cursor: 17 },
  );
});
