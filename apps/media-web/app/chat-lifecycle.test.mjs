import assert from 'node:assert/strict';
import test from 'node:test';

import { canStartNewConversation, isCurrentConversation } from './chat-lifecycle.ts';

test('new chat remains available while MedIA is working', () => {
  assert.equal(canStartNewConversation({ deleting: false, hasConversation: true, hasJobs: true, submitting: false }), true);
});

test('new chat remains available while the first request is still submitting', () => {
  assert.equal(canStartNewConversation({ deleting: false, hasConversation: false, hasJobs: false, submitting: true }), true);
});

test('new chat is disabled only when there is nothing to clear or deletion is in flight', () => {
  assert.equal(canStartNewConversation({ deleting: false, hasConversation: false, hasJobs: false, submitting: false }), false);
  assert.equal(canStartNewConversation({ deleting: true, hasConversation: true, hasJobs: true, submitting: false }), false);
});

test('late responses from the deleted conversation are ignored', () => {
  assert.equal(isCurrentConversation(3, 4), false);
  assert.equal(isCurrentConversation(4, 4), true);
});
