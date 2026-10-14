import { describe, expect, it } from 'vitest';
import {
  chatReducer,
  filterInteractions,
  gameIdError,
  highlightSegments,
  initialChatState,
  isValidGameId,
  replyStatus,
  replyStatusTone,
  truncateOutput,
} from '@/lib/bot';
import type { Interaction } from '@/api/types';

describe('isValidGameId / gameIdError', () => {
  it('accepts 3–16 letters/digits/underscores', () => {
    expect(isValidGameId('abc')).toBe(true);
    expect(isValidGameId('Mickey_Miao2000')).toBe(true);
    expect(isValidGameId('a_B_9')).toBe(true);
  });
  it('rejects too short / too long', () => {
    expect(isValidGameId('ab')).toBe(false);
    expect(isValidGameId('a'.repeat(17))).toBe(false);
  });
  it('rejects illegal characters', () => {
    expect(isValidGameId('bad-name')).toBe(false);
    expect(isValidGameId('空格 ')).toBe(false);
    expect(isValidGameId('name!')).toBe(false);
  });
  it('gameIdError messages', () => {
    expect(gameIdError('')).toBe('请输入游戏 ID');
    expect(gameIdError('ab')).toContain('3–16');
    expect(gameIdError('ValidName')).toBeNull();
  });
});

describe('replyStatus / replyStatusTone', () => {
  it('maps emoji prefixes to outcome', () => {
    expect(replyStatus('✅ 已添加白名单')).toBe('success');
    expect(replyStatus('⚠️ 部分成功：已写入但重载失败')).toBe('partial');
    expect(replyStatus('❌ 玩家不存在')).toBe('failure');
  });
  it('treats non-emoji prefix as unknown', () => {
    expect(replyStatus('已添加')).toBe('unknown');
    expect(replyStatus('')).toBe('unknown');
  });
  it('ignores leading whitespace', () => {
    expect(replyStatus('   ✅ ok')).toBe('success');
  });
  it('handles the bare ⚠ codepoint too', () => {
    expect(replyStatus('⚠ 注意')).toBe('partial');
  });
  it('maps status to tone', () => {
    expect(replyStatusTone('success')).toBe('green');
    expect(replyStatusTone('partial')).toBe('yellow');
    expect(replyStatusTone('failure')).toBe('red');
    expect(replyStatusTone('unknown')).toBe('gray');
  });
});

describe('chatReducer (simulator history)', () => {
  it('appends a user turn on send', () => {
    const s = chatReducer(initialChatState, { type: 'send', input: '状态' });
    expect(s.turns).toHaveLength(1);
    expect(s.turns[0]).toMatchObject({ id: 1, role: 'user', text: '状态' });
    expect(s.seq).toBe(1);
  });
  it('appends a bot turn with derived status on reply', () => {
    let s = chatReducer(initialChatState, { type: 'send', input: '怎么加入' });
    s = chatReducer(s, { type: 'reply', reply: '✅ 已把你加入白名单' });
    expect(s.turns).toHaveLength(2);
    expect(s.turns[1]).toMatchObject({ id: 2, role: 'bot', status: 'success' });
    expect(s.turns[1]!.text).toBe('✅ 已把你加入白名单');
  });
  it('keeps ids monotonic and does not mutate prior state', () => {
    const s1 = chatReducer(initialChatState, { type: 'send', input: 'a' });
    const s2 = chatReducer(s1, { type: 'reply', reply: 'b' });
    expect(s1.turns).toHaveLength(1); // s1 unchanged
    expect(s2.turns.map((t) => t.id)).toEqual([1, 2]);
  });
  it('reset clears history', () => {
    let s = chatReducer(initialChatState, { type: 'send', input: 'x' });
    s = chatReducer(s, { type: 'reset' });
    expect(s).toEqual(initialChatState);
  });
});

function interaction(partial: Partial<Interaction> & { id: number }): Interaction {
  return {
    ts: '2026-10-09T00:00:00Z',
    group_masked: 'E804...5786',
    openid_masked: 'A1B2...9F0E',
    admin: false,
    cmd: 'status',
    input: '',
    output: '',
    ...partial,
  };
}

describe('interactionMatches / filterInteractions', () => {
  const items = [
    interaction({ id: 1, cmd: 'status', input: '状态', output: '服务器在线' }),
    interaction({ id: 2, cmd: 'join', input: '怎么加入', output: '发送你的游戏ID' }),
    interaction({ id: 3, cmd: 'mods', input: 'mod列表', output: 'carpet, lithium' }),
  ];
  it('empty query matches everything', () => {
    expect(filterInteractions(items, '').map((i) => i.id)).toEqual([1, 2, 3]);
    expect(filterInteractions(items, '   ').map((i) => i.id)).toEqual([1, 2, 3]);
  });
  it('matches on input', () => {
    expect(filterInteractions(items, '加入').map((i) => i.id)).toEqual([2]);
  });
  it('matches on output', () => {
    expect(filterInteractions(items, 'carpet').map((i) => i.id)).toEqual([3]);
  });
  it('matches on cmd, case-insensitively', () => {
    expect(filterInteractions(items, 'STATUS').map((i) => i.id)).toEqual([1]);
  });
  it('no match returns empty', () => {
    expect(filterInteractions(items, 'nonexistent')).toEqual([]);
  });
});

describe('highlightSegments', () => {
  it('empty query -> single non-match segment', () => {
    expect(highlightSegments('hello', '')).toEqual([{ text: 'hello', match: false }]);
  });
  it('splits around a single match preserving case', () => {
    expect(highlightSegments('Carpet mod', 'carpet')).toEqual([
      { text: 'Carpet', match: true },
      { text: ' mod', match: false },
    ]);
  });
  it('finds multiple occurrences', () => {
    expect(highlightSegments('abXabY', 'ab')).toEqual([
      { text: 'ab', match: true },
      { text: 'X', match: false },
      { text: 'ab', match: true },
      { text: 'Y', match: false },
    ]);
  });
  it('leading match has no empty prefix segment', () => {
    expect(highlightSegments('abc', 'ab')).toEqual([
      { text: 'ab', match: true },
      { text: 'c', match: false },
    ]);
  });
  it('no match -> whole string, unmatched', () => {
    expect(highlightSegments('hello', 'zzz')).toEqual([{ text: 'hello', match: false }]);
  });
});

describe('truncateOutput', () => {
  it('leaves short text alone', () => {
    expect(truncateOutput('short', 120)).toEqual({ text: 'short', truncated: false });
  });
  it('truncates long text', () => {
    const long = 'x'.repeat(200);
    const r = truncateOutput(long, 120);
    expect(r.truncated).toBe(true);
    expect(r.text).toHaveLength(120);
  });
  it('boundary: exactly max is not truncated', () => {
    const s = 'y'.repeat(10);
    expect(truncateOutput(s, 10)).toEqual({ text: s, truncated: false });
  });
});
