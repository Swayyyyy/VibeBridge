import type { ChatMessage } from '../types/types';
import { decodeHtmlEntities, unescapeWithMathProtection } from './chatFormatting';

export interface DiffLine {
  type: 'added' | 'removed';
  content: string;
  lineNum: number;
}

export type DiffCalculator = (oldStr: string, newStr: string) => DiffLine[];

const normalizeToolInput = (value: unknown): string => {
  if (value === null || value === undefined || value === '') {
    return '';
  }

  if (typeof value === 'string') {
    return value;
  }

  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
};

const inferClaudeContentPartType = (part: unknown): string | null => {
  if (!part || typeof part !== 'object') {
    return null;
  }

  const candidate = part as Record<string, any>;
  if (typeof candidate.type === 'string' && candidate.type.trim()) {
    return candidate.type;
  }

  if (typeof candidate.text === 'string') {
    return 'text';
  }

  if (typeof candidate.thinking === 'string') {
    return 'thinking';
  }

  if (candidate.tool_use_id) {
    return 'tool_result';
  }

  if (candidate.id && candidate.name) {
    return 'tool_use';
  }

  return null;
};

const extractCompactionSummary = (message: any): string => {
  const candidate =
    message.message?.content ??
    message.summary ??
    message.compactionSummary ??
    message.content;

  if (typeof candidate !== 'string') {
    return '';
  }

  return unescapeWithMathProtection(decodeHtmlEntities(candidate));
};

export const calculateDiff = (oldStr: string, newStr: string): DiffLine[] => {
  const oldLines = oldStr.split('\n');
  const newLines = newStr.split('\n');

  // Use LCS alignment so insertions/deletions don't cascade into a full-file "changed" diff.
  const lcsTable: number[][] = Array.from({ length: oldLines.length + 1 }, () =>
    new Array<number>(newLines.length + 1).fill(0),
  );
  for (let oldIndex = oldLines.length - 1; oldIndex >= 0; oldIndex -= 1) {
    for (let newIndex = newLines.length - 1; newIndex >= 0; newIndex -= 1) {
      if (oldLines[oldIndex] === newLines[newIndex]) {
        lcsTable[oldIndex][newIndex] = lcsTable[oldIndex + 1][newIndex + 1] + 1;
      } else {
        lcsTable[oldIndex][newIndex] = Math.max(
          lcsTable[oldIndex + 1][newIndex],
          lcsTable[oldIndex][newIndex + 1],
        );
      }
    }
  }

  const diffLines: DiffLine[] = [];
  let oldIndex = 0;
  let newIndex = 0;

  while (oldIndex < oldLines.length && newIndex < newLines.length) {
    const oldLine = oldLines[oldIndex];
    const newLine = newLines[newIndex];

    if (oldLine === newLine) {
      oldIndex += 1;
      newIndex += 1;
      continue;
    }

    if (lcsTable[oldIndex + 1][newIndex] >= lcsTable[oldIndex][newIndex + 1]) {
      diffLines.push({ type: 'removed', content: oldLine, lineNum: oldIndex + 1 });
      oldIndex += 1;
      continue;
    }

    diffLines.push({ type: 'added', content: newLine, lineNum: newIndex + 1 });
    newIndex += 1;
  }

  while (oldIndex < oldLines.length) {
    diffLines.push({ type: 'removed', content: oldLines[oldIndex], lineNum: oldIndex + 1 });
    oldIndex += 1;
  }

  while (newIndex < newLines.length) {
    diffLines.push({ type: 'added', content: newLines[newIndex], lineNum: newIndex + 1 });
    newIndex += 1;
  }

  return diffLines;
};

export const createCachedDiffCalculator = (): DiffCalculator => {
  const cache = new Map<string, DiffLine[]>();

  return (oldStr: string, newStr: string) => {
    const key = JSON.stringify([oldStr, newStr]);
    const cached = cache.get(key);
    if (cached) {
      return cached;
    }

    const calculated = calculateDiff(oldStr, newStr);
    cache.set(key, calculated);
    if (cache.size > 100) {
      const firstKey = cache.keys().next().value;
      if (firstKey) {
        cache.delete(firstKey);
      }
    }
    return calculated;
  };
};

export const convertSessionMessages = (rawMessages: any[]): ChatMessage[] => {
  const converted: ChatMessage[] = [];
  const toolResults = new Map<
    string,
    { content: unknown; isError: boolean; timestamp: Date; toolUseResult: unknown; subagentTools?: unknown[] }
  >();

  rawMessages.forEach((message) => {
    if (message.message?.role === 'user' && Array.isArray(message.message?.content)) {
      message.message.content.forEach((part: any) => {
        if (inferClaudeContentPartType(part) !== 'tool_result') {
          return;
        }
        toolResults.set(part.tool_use_id, {
          content: part.content,
          isError: Boolean(part.is_error),
          timestamp: new Date(message.timestamp || Date.now()),
          toolUseResult: message.toolUseResult || null,
          subagentTools: message.subagentTools,
        });
      });
    }
  });

  rawMessages.forEach((message) => {
    if (message.message?.role === 'user' && message.message?.content) {
      let content = '';
      if (Array.isArray(message.message.content)) {
        const textParts: string[] = [];
        message.message.content.forEach((part: any) => {
          if (inferClaudeContentPartType(part) === 'text') {
            textParts.push(decodeHtmlEntities(part.text));
          }
        });
        content = textParts.join('\n');
      } else if (typeof message.message.content === 'string') {
        content = decodeHtmlEntities(message.message.content);
      } else {
        content = decodeHtmlEntities(String(message.message.content));
      }

      const shouldSkip =
        !content ||
        content.startsWith('<command-name>') ||
        content.startsWith('<command-message>') ||
        content.startsWith('<command-args>') ||
        content.startsWith('<local-command-stdout>') ||
        content.startsWith('<system-reminder>') ||
        content.startsWith('Caveat:') ||
        content.startsWith('This session is being continued from a previous') ||
        content.startsWith('[Request interrupted');

      if (!shouldSkip) {
        // Parse <task-notification> blocks into compact system messages
        const taskNotifRegex = /<task-notification>\s*<task-id>[^<]*<\/task-id>\s*<output-file>[^<]*<\/output-file>\s*<status>([^<]*)<\/status>\s*<summary>([^<]*)<\/summary>\s*<\/task-notification>/g;
        const taskNotifMatch = taskNotifRegex.exec(content);
        if (taskNotifMatch) {
          const status = taskNotifMatch[1]?.trim() || 'completed';
          const summary = taskNotifMatch[2]?.trim() || 'Background task finished';
          converted.push({
            type: 'assistant',
            content: summary,
            timestamp: message.timestamp || new Date().toISOString(),
            isTaskNotification: true,
            taskStatus: status,
          });
        } else {
          converted.push({
            type: 'user',
            content: unescapeWithMathProtection(content),
            timestamp: message.timestamp || new Date().toISOString(),
          });
        }
      }
      return;
    }

    if (message.type === 'compaction_status') {
      const compactionSummary = extractCompactionSummary(message);
      converted.push({
        type: 'assistant',
        content: compactionSummary,
        timestamp: message.timestamp || new Date().toISOString(),
        isCompactionStatus: true,
        compactionState: message.status === 'compacting' ? 'compacting' : 'compacted',
        compactionSummary: compactionSummary || undefined,
      });
      return;
    }

    if (message.type === 'thinking' && message.message?.content) {
      converted.push({
        type: 'assistant',
        content: unescapeWithMathProtection(message.message.content),
        timestamp: message.timestamp || new Date().toISOString(),
        isThinking: true,
      });
      return;
    }

    if (message.type === 'tool_use' && message.toolName) {
      converted.push({
        type: 'assistant',
        content: '',
        timestamp: message.timestamp || new Date().toISOString(),
        isToolUse: true,
        toolName: message.toolName,
        toolInput: normalizeToolInput(message.toolInput),
        toolCallId: message.toolCallId,
      });
      return;
    }

    if (message.type === 'tool_result') {
      for (let index = converted.length - 1; index >= 0; index -= 1) {
        const convertedMessage = converted[index];
        if (!convertedMessage.isToolUse || convertedMessage.toolResult) {
          continue;
        }
        if (!message.toolCallId || convertedMessage.toolCallId === message.toolCallId) {
          convertedMessage.toolResult = {
            content: message.output || '',
            isError: false,
          };
          break;
        }
      }
      return;
    }

    if (message.message?.role === 'assistant' && message.message?.content) {
      if (Array.isArray(message.message.content)) {
        message.message.content.forEach((part: any) => {
          const partType = inferClaudeContentPartType(part);

          if (partType === 'text') {
            let text = part.text;
            if (typeof text === 'string') {
              text = unescapeWithMathProtection(text);
            }
            converted.push({
              type: 'assistant',
              content: text,
              timestamp: message.timestamp || new Date().toISOString(),
            });
            return;
          }

          if (partType === 'tool_use') {
            const toolResult = toolResults.get(part.id);
            const isSubagentContainer = part.name === 'Task';

            // Build child tools from server-provided subagentTools data
            const childTools: import('../types/types').SubagentChildTool[] = [];
            if (isSubagentContainer && toolResult?.subagentTools && Array.isArray(toolResult.subagentTools)) {
              for (const tool of toolResult.subagentTools as any[]) {
                childTools.push({
                  toolId: tool.toolId,
                  toolName: tool.toolName,
                  toolInput: tool.toolInput,
                  toolResult: tool.toolResult || null,
                  timestamp: new Date(tool.timestamp || Date.now()),
                });
              }
            }

            converted.push({
              type: 'assistant',
              content: '',
              timestamp: message.timestamp || new Date().toISOString(),
              isToolUse: true,
              toolName: part.name,
              toolInput: normalizeToolInput(part.input),
              toolId: part.id,
              toolResult: toolResult
                ? {
                    content:
                      typeof toolResult.content === 'string'
                        ? toolResult.content
                        : JSON.stringify(toolResult.content),
                    isError: toolResult.isError,
                    toolUseResult: toolResult.toolUseResult,
                  }
                : null,
              toolError: toolResult?.isError || false,
              toolResultTimestamp: toolResult?.timestamp || new Date(),
              isSubagentContainer,
              subagentState: isSubagentContainer
                ? {
                    childTools,
                    currentToolIndex: childTools.length > 0 ? childTools.length - 1 : -1,
                    isComplete: Boolean(toolResult),
                  }
                : undefined,
            });
          }
        });
        return;
      }

      if (typeof message.message.content === 'string') {
        converted.push({
          type: 'assistant',
          content: unescapeWithMathProtection(message.message.content),
          timestamp: message.timestamp || new Date().toISOString(),
        });
      }
    }
  });

  return converted;
};
