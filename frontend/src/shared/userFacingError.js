// Product-wide error copy. Keep diagnostic data and control-flow codes internal.
export const USER_FACING_ERROR = '当前网络连接失败';

export function userFacingError(error) {
  return error ? USER_FACING_ERROR : '';
}

// Project only explicitly marked failures, never rewrite a user's quoted error.
export function userFacingMessageContent(message) {
  if (message?.role !== 'user' && (message?.error || ['failed', 'error', 'interrupted'].includes(message?.status))) {
    return USER_FACING_ERROR;
  }
  return typeof message?.content === 'string' ? message.content : JSON.stringify(message?.content ?? '');
}
