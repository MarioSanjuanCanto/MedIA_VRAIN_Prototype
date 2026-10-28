export type NewConversationState = {
  deleting: boolean;
  hasConversation: boolean;
  hasJobs: boolean;
  submitting: boolean;
};

export function canStartNewConversation({ deleting, hasConversation, hasJobs, submitting }: NewConversationState) {
  return !deleting && (hasConversation || hasJobs || submitting);
}

export function isCurrentConversation(operationEpoch: number, currentEpoch: number) {
  return operationEpoch === currentEpoch;
}
