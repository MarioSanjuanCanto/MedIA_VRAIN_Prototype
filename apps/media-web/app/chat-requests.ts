type CommandDefinitions = Record<string, { prompt: string; emptyInput: string }>;

export function buildChatRequest(message: string, definitions: CommandDefinitions): {
  message: string;
  command_prompt?: string;
} {
  let clean = message.trim();
  // Recover commands from jobs stored by the earlier frontend when retrying.
  for (const [name, definition] of Object.entries(definitions)) {
    if (!definition.prompt.includes('{{input}}')) continue;
    const prefix = definition.prompt.split('{{input}}')[0];
    if (prefix && clean.startsWith(prefix)) {
      clean = `${name} ${clean.slice(prefix.length)}`.trim();
      break;
    }
  }
  const match = /^(\/\S+)(?:\s+([\s\S]*))?$/.exec(clean);
  const name = match?.[1].toLowerCase();
  const definition = name ? definitions[name] : undefined;
  if (!definition || name === '/clips-todos') return { message: clean };
  return {
    message: clean,
    command_prompt: definition.prompt.replace('{{input}}', match?.[2]?.trim() || definition.emptyInput),
  };
}
