// Pure logic for the M2 QQ bot / whitelist pages. No React, fully unit-tested.

import type { BadgeTone } from '@/components/ui/Badge';
import type { Interaction } from '@/api/types';

// --- game-ID validation ------------------------------------------------------

/** Minecraft Java 用户名字符集与长度（任务约定）。区分大小写由调用方提示用户。 */
export const GAME_ID_RE = /^[A-Za-z0-9_]{3,16}$/;

export function isValidGameId(name: string): boolean {
  return GAME_ID_RE.test(name);
}

/** 校验失败时的中文提示；通过时返回 null。 */
export function gameIdError(name: string): string | null {
  if (name.length === 0) return '请输入游戏 ID';
  if (!GAME_ID_RE.test(name)) return '仅限 3–16 位字母、数字或下划线';
  return null;
}

// --- reply-prefix -> outcome -------------------------------------------------

export type ReplyStatus = 'success' | 'partial' | 'failure' | 'unknown';

/**
 * 机器人风格 reply 文本的首字符编码结果：
 * ✅ 成功 / ⚠️ 部分成功 / ❌ 失败；其余视为 unknown（仍原样展示）。
 * 使用 Array.from 以正确处理 emoji 的码点（避免代理对半截）。
 */
export function replyStatus(reply: string): ReplyStatus {
  const first = Array.from(reply.trimStart())[0];
  switch (first) {
    case '✅':
      return 'success';
    case '⚠️':
    case '⚠':
      return 'partial';
    case '❌':
      return 'failure';
    default:
      return 'unknown';
  }
}

export function replyStatusTone(status: ReplyStatus): BadgeTone {
  switch (status) {
    case 'success':
      return 'green';
    case 'partial':
      return 'yellow';
    case 'failure':
      return 'red';
    case 'unknown':
      return 'gray';
  }
}

// --- simulator chat-history reducer ------------------------------------------

export interface ChatTurn {
  id: number;
  role: 'user' | 'bot';
  text: string;
  /** bot 回复才有：由 replyStatus 推导，用于气泡着色。 */
  status?: ReplyStatus;
}

export type ChatAction =
  | { type: 'send'; input: string }
  | { type: 'reply'; reply: string }
  | { type: 'reset' };

export interface ChatState {
  turns: ChatTurn[];
  seq: number;
}

export const initialChatState: ChatState = { turns: [], seq: 0 };

/**
 * Reducer for the simulator conversation. `send` appends the user turn
 * optimistically; `reply` appends the bot turn once the server responds.
 * Pure and id-stable so it is trivially unit-testable.
 */
export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'send': {
      const id = state.seq + 1;
      return {
        seq: id,
        turns: [...state.turns, { id, role: 'user', text: action.input }],
      };
    }
    case 'reply': {
      const id = state.seq + 1;
      return {
        seq: id,
        turns: [
          ...state.turns,
          { id, role: 'bot', text: action.reply, status: replyStatus(action.reply) },
        ],
      };
    }
    case 'reset':
      return initialChatState;
  }
}

// --- interaction search: match / highlight / truncate ------------------------

/** 交互记录按搜索词匹配 input/output/cmd（大小写不敏感）。空词匹配全部。 */
export function interactionMatches(it: Interaction, q: string): boolean {
  const term = q.trim().toLowerCase();
  if (term === '') return true;
  return (
    it.input.toLowerCase().includes(term) ||
    it.output.toLowerCase().includes(term) ||
    it.cmd.toLowerCase().includes(term)
  );
}

export function filterInteractions(items: Interaction[], q: string): Interaction[] {
  return items.filter((it) => interactionMatches(it, q));
}

export interface HighlightSegment {
  text: string;
  match: boolean;
}

/**
 * 把文本切分成命中/非命中片段，供 UI 高亮渲染。空词返回单一非命中片段。
 * 大小写不敏感，保留原文大小写。
 */
export function highlightSegments(text: string, q: string): HighlightSegment[] {
  const term = q.trim();
  if (term === '') return [{ text, match: false }];
  const lowerText = text.toLowerCase();
  const lowerTerm = term.toLowerCase();
  const segments: HighlightSegment[] = [];
  let i = 0;
  while (i < text.length) {
    const hit = lowerText.indexOf(lowerTerm, i);
    if (hit === -1) {
      segments.push({ text: text.slice(i), match: false });
      break;
    }
    if (hit > i) segments.push({ text: text.slice(i, hit), match: false });
    segments.push({ text: text.slice(hit, hit + term.length), match: true });
    i = hit + term.length;
  }
  return segments;
}

/** 长输出截断：超过 max 返回 {text, truncated:true}，否则原样。 */
export function truncateOutput(text: string, max = 120): { text: string; truncated: boolean } {
  if (text.length <= max) return { text, truncated: false };
  return { text: text.slice(0, max), truncated: true };
}
