export function insertIntoDraft(
  draft: string,
  value: string,
  selectionStart = draft.length,
  selectionEnd = selectionStart,
) {
  const start = Math.max(0, Math.min(selectionStart, draft.length));
  const end = Math.max(start, Math.min(selectionEnd, draft.length));
  const needsLeadingSpace = start > 0 && !/\s/.test(draft[start - 1]);
  const needsTrailingSpace = end < draft.length && !/\s/.test(draft[end]);
  const inserted = `${needsLeadingSpace ? ' ' : ''}${value}${needsTrailingSpace ? ' ' : ''}`;

  return {
    draft: draft.slice(0, start) + inserted + draft.slice(end),
    cursor: start + inserted.length,
  };
}
