// The home composer and the skill-pool form submit the same authoring contract.
// Preserve the user's words exactly; only a genuinely empty field uses the form's fallback.
export function authoringCreateRequest({
  kind = 'app', intent = 'create', mode = 'prepare', instruction = '', fallbackInstruction = '',
  sourceRefs = [], fileIds = [], importedId = '', conversationId, target,
}) {
  const sources = [...sourceRefs, ...fileIds.map(fileId => ({ kind: 'upload', reference: { fileId } }))];
  if (importedId) sources.push({ kind: 'import_draft', reference: { portableAssetId: importedId } });
  return {
    kind, intent: importedId ? 'prepare_import' : intent,
    instruction: instruction.trim() ? instruction : fallbackInstruction,
    mode, sourceRefs: sources,
    ...(conversationId !== undefined ? { conversationId } : {}),
    ...(target ? { target } : {}),
  };
}
