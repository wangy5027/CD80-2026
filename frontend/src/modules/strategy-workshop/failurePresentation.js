import { USER_FACING_ERROR } from '../../shared/userFacingError.js';

// Historical failure entries retain their diagnostics in storage. Mask only
// the visible copy, including records saved before the common error wording.
export function isRecordedFailure(entry) {
  return entry?.role === 'assistant' && (
    String(entry.source || '').startsWith('failure:') ||
    Boolean(entry.details?.error) ||
    entry.details?.customization?.status === 'validation_failed' ||
    entry.details?.customization?.validation?.passed === false ||
    entry.title === '当前步骤未完成'
  );
}

export function recordedEntryText(entry) {
  return isRecordedFailure(entry) ? USER_FACING_ERROR : entry?.text || '';
}

export function publicValidationTrace(trace) {
  return trace?.validation?.passed === true ? trace : null;
}
