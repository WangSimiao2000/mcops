import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ApiError } from '@/api/http';
import {
  logsApi,
  type LogLevel,
  type LogLine,
  type LogSources,
  type SearchHit,
} from '@/api/logs';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { PageHeader } from '@/components/PageHeader';
import { scrollBox } from '@/lib/scroll';
import { formatBytes } from '@/lib/ops';
import { formatDateTime, formatTime } from '@/lib/format';

const LEVELS: LogLevel[] = ['INFO', 'WARN', 'ERROR'];
const TAIL_OPTIONS = [200, 500, 2000, 5000];
const AUTO_REFRESH_MS = 5000;

/** latest.log / journal 源支持自动刷新。 */
function isLive(source: string): boolean {
  return source === 'latest.log' || source.startsWith('journal:');
}

function sourceLabel(source: string): string {
  if (source === 'latest.log') return 'latest.log（当前）';
  if (source === 'journal:agent') return '系统日志：agent';
  if (source === 'journal:server') return '系统日志：server';
  if (source.startsWith('crash:')) return source.slice('crash:'.length);
  return source;
}

/** 防抖：延迟返回稳定值。 */
function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
}

const LEVEL_TEXT: Record<LogLevel, string> = {
  INFO: 'text-slate-700 dark:text-slate-300',
  WARN: 'text-yellow-700 dark:text-yellow-400',
  ERROR: 'text-red-600 dark:text-red-400',
};

/** 关键词高亮（大小写不敏感；正则模式下不高亮，避免误解析）。 */
function highlight(text: string, q: string): React.ReactNode {
  if (!q) return text;
  const lower = text.toLowerCase();
  const needle = q.toLowerCase();
  const parts: React.ReactNode[] = [];
  let i = 0;
  let key = 0;
  for (;;) {
    const idx = lower.indexOf(needle, i);
    if (idx === -1) {
      parts.push(text.slice(i));
      break;
    }
    if (idx > i) parts.push(text.slice(i, idx));
    parts.push(
      <mark key={key++} className="bg-yellow-200 text-inherit dark:bg-yellow-700/60">
        {text.slice(idx, idx + needle.length)}
      </mark>,
    );
    i = idx + needle.length;
  }
  return parts;
}

/** 单行组件，memo 以避免整表重渲染。 */
const LineRow = memo(function LineRow({
  line,
  highlightQuery,
}: {
  line: LogLine;
  highlightQuery: string;
}) {
  const tone = line.level ? LEVEL_TEXT[line.level] : 'text-slate-500 dark:text-slate-400';
  return (
    <div className="flex gap-3 px-3 leading-5 hover:bg-slate-50 dark:hover:bg-slate-800/40">
      <span className="w-12 shrink-0 select-none text-right text-slate-400 tabular-nums">
        {line.n}
      </span>
      <span className={`whitespace-pre-wrap break-all ${tone}`}>
        {line.continuation ? <span className="text-slate-400">│ </span> : null}
        {highlightQuery ? highlight(line.text, highlightQuery) : line.text}
      </span>
    </div>
  );
});

// ---- 源列表 --------------------------------------------------------------

function SourceButton({
  id,
  label,
  active,
  meta,
  disabled,
  onClick,
}: {
  id: string;
  label: string;
  active: boolean;
  meta?: string;
  disabled?: boolean;
  onClick: (id: string) => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onClick(id)}
      className={
        'flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left text-sm ' +
        (active
          ? 'bg-brand text-brand-fg'
          : 'text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800') +
        (disabled ? ' cursor-not-allowed opacity-50' : '')
      }
    >
      <span className="truncate">{label}</span>
      {meta ? <span className="shrink-0 text-xs opacity-70">{meta}</span> : null}
    </button>
  );
}

function SourceList({
  sources,
  current,
  onSelect,
}: {
  sources: LogSources;
  current: string;
  onSelect: (id: string) => void;
}) {
  // 游戏日志按日期分组，折叠显示（latest 单列最上面）。
  const byDate = useMemo(() => {
    const groups = new Map<string, typeof sources.game>();
    for (const g of sources.game) {
      if (g.latest) continue;
      const key = g.date ?? '未知日期';
      const arr = groups.get(key) ?? [];
      arr.push(g);
      groups.set(key, arr);
    }
    return [...groups.entries()].sort((a, b) => b[0].localeCompare(a[0]));
  }, [sources]);

  const latest = sources.game.find((g) => g.latest);
  const [openDate, setOpenDate] = useState<string | null>(null);

  return (
    <div className="space-y-4">
      <section>
        <h3 className="mb-1 px-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
          游戏日志
        </h3>
        {latest ? (
          <SourceButton
            id="latest.log"
            label="latest.log（当前）"
            meta={formatBytes(latest.size)}
            active={current === 'latest.log'}
            onClick={onSelect}
          />
        ) : null}
        <div
          className={scrollBox('md', 'mt-1')}
          tabIndex={0}
          role="region"
          aria-label={`历史游戏日志（${byDate.length} 天）`}
        >
        {byDate.map(([d, files]) => (
          <div key={d}>
            <button
              type="button"
              onClick={() => setOpenDate((v) => (v === d ? null : d))}
              className="flex w-full items-center gap-1 rounded px-2 py-1 text-left text-sm text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
            >
              <span className="w-3 text-xs">{openDate === d ? '▾' : '▸'}</span>
              <span>{d}</span>
              <span className="ml-auto text-xs opacity-60">{files.length}</span>
            </button>
            {openDate === d ? (
              <div className="ml-4 space-y-0.5">
                {files
                  .slice()
                  .sort((a, b) => b.seq - a.seq)
                  .map((f) => (
                    <SourceButton
                      key={f.name}
                      id={f.name}
                      label={f.name}
                      meta={formatBytes(f.size)}
                      active={current === f.name}
                      onClick={onSelect}
                    />
                  ))}
              </div>
            ) : null}
          </div>
        ))}
        </div>
      </section>

      <section>
        <h3 className="mb-1 px-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
          崩溃报告
        </h3>
        {sources.crash.length === 0 ? (
          <p className="px-2 text-xs text-slate-400">暂无崩溃报告</p>
        ) : (
          <div className={scrollBox('sm')} tabIndex={0} role="region" aria-label="崩溃报告">
          {sources.crash.map((c) => (
            <SourceButton
              key={c.name}
              id={`crash:${c.name}`}
              label={c.name}
              meta={formatBytes(c.size)}
              active={current === `crash:${c.name}`}
              onClick={onSelect}
            />
          ))}
          </div>
        )}
      </section>

      <section>
        <h3 className="mb-1 px-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
          系统日志
        </h3>
        {sources.journal.map((j) => (
          <SourceButton
            key={j.name}
            id={`journal:${j.name}`}
            label={j.name === 'agent' ? 'agent（面板）' : 'server（MC）'}
            active={current === `journal:${j.name}`}
            disabled={!j.available}
            meta={j.available ? undefined : '不可用'}
            onClick={onSelect}
          />
        ))}
      </section>
    </div>
  );
}

// ---- 查看器 --------------------------------------------------------------

function Viewer({
  source,
  jumpToLine,
}: {
  source: string;
  jumpToLine: number | null;
}) {
  const [levels, setLevels] = useState<LogLevel[]>([]);
  const [keyword, setKeyword] = useState('');
  const [tail, setTail] = useState(500);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const debouncedKeyword = useDebounced(keyword, 300);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const pinnedToBottom = useRef(true);

  const live = isLive(source);
  // 切换源时重置自动刷新（非 live 源不支持）。
  useEffect(() => {
    if (!live) setAutoRefresh(false);
  }, [source, live]);

  const viewQuery = useQuery({
    queryKey: ['logs', 'view', source, tail, levels.join(','), debouncedKeyword],
    queryFn: ({ signal }) =>
      logsApi.view(
        {
          source,
          tail,
          ...(levels.length > 0 ? { level: levels } : {}),
          ...(debouncedKeyword ? { q: debouncedKeyword } : {}),
        },
        signal,
      ),
    refetchInterval: autoRefresh ? AUTO_REFRESH_MS : false,
    retry: false,
  });

  const lines = useMemo(() => viewQuery.data?.lines ?? [], [viewQuery.data]);

  // 自动滚到底部：仅当用户停在底部时（往上滚后暂停）。
  function onScroll() {
    const el = scrollRef.current;
    if (!el) return;
    pinnedToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
  }
  useEffect(() => {
    const el = scrollRef.current;
    if (el && autoRefresh && pinnedToBottom.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [lines, autoRefresh]);

  // 搜索跳转：定位到目标行附近。
  useEffect(() => {
    if (jumpToLine == null) return;
    const el = scrollRef.current;
    if (!el) return;
    const target = el.querySelector(`[data-n="${jumpToLine}"]`);
    if (target instanceof HTMLElement) {
      target.scrollIntoView({ block: 'center' });
      target.classList.add('ring-2', 'ring-brand');
      setTimeout(() => target.classList.remove('ring-2', 'ring-brand'), 1500);
    }
  }, [jumpToLine, lines]);

  function toggleLevel(l: LogLevel) {
    setLevels((cur) => (cur.includes(l) ? cur.filter((x) => x !== l) : [...cur, l]));
  }

  const err = viewQuery.error;

  return (
    <Card>
      <CardHeader
        title={<span className="font-mono text-sm">{sourceLabel(source)}</span>}
        action={
          <div className="flex items-center gap-2">
            {viewQuery.data?.truncated ? <Badge tone="gray">已截断</Badge> : null}
            <a
              href={live && source.startsWith('journal:') ? undefined : logsApi.downloadUrl(source)}
              aria-disabled={source.startsWith('journal:')}
              className={
                source.startsWith('journal:')
                  ? 'pointer-events-none opacity-40'
                  : ''
              }
            >
              <Button size="sm" variant="secondary" disabled={source.startsWith('journal:')}>
                下载
              </Button>
            </a>
          </div>
        }
      />
      <CardBody className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex gap-1" role="group" aria-label="级别过滤">
            {LEVELS.map((l) => (
              <button
                key={l}
                type="button"
                aria-pressed={levels.includes(l)}
                onClick={() => toggleLevel(l)}
                className={
                  'rounded px-2 py-1 text-xs font-medium ' +
                  (levels.includes(l)
                    ? 'bg-brand text-brand-fg'
                    : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300')
                }
              >
                {l}
              </button>
            ))}
          </div>
          <input
            type="search"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="关键词过滤…"
            aria-label="关键词过滤"
            className="min-w-40 flex-1 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-950"
          />
          <label className="flex items-center gap-1 text-sm text-slate-600 dark:text-slate-300">
            行数
            <select
              value={tail}
              onChange={(e) => setTail(Number(e.target.value))}
              aria-label="tail 行数"
              className="rounded-md border border-slate-300 bg-white px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-950"
            >
              {TAIL_OPTIONS.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          {live ? (
            <label className="flex items-center gap-1 text-sm text-slate-600 dark:text-slate-300">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
              />
              自动刷新
            </label>
          ) : null}
        </div>

        {err ? (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {err instanceof ApiError ? err.detail : '读取日志失败'}
          </p>
        ) : null}

        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="h-[32rem] overflow-auto rounded-md border border-slate-200 bg-slate-50 py-1 font-mono text-xs dark:border-slate-800 dark:bg-slate-950"
        >
          {viewQuery.isLoading ? (
            <p className="px-3 py-2 text-slate-400">加载中…</p>
          ) : lines.length === 0 ? (
            <p className="px-3 py-2 text-slate-400">无匹配的日志行</p>
          ) : (
            lines.map((line) => (
              <div key={line.n} data-n={line.n}>
                <LineRow line={line} highlightQuery={debouncedKeyword} />
              </div>
            ))
          )}
        </div>
      </CardBody>
    </Card>
  );
}

// ---- 跨文件搜索面板 --------------------------------------------------------

function SearchPanel({ onJump }: { onJump: (source: string, line: number) => void }) {
  const [q, setQ] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [submitted, setSubmitted] = useState<{ q: string; from: string; to: string } | null>(null);

  const searchQuery = useQuery({
    queryKey: ['logs', 'search', submitted],
    enabled: submitted !== null,
    queryFn: ({ signal }) =>
      logsApi.search(
        {
          q: submitted!.q,
          ...(submitted!.from ? { from: submitted!.from } : {}),
          ...(submitted!.to ? { to: submitted!.to } : {}),
        },
        signal,
      ),
    retry: false,
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (q.trim()) setSubmitted({ q: q.trim(), from, to });
  }

  const hits: SearchHit[] = searchQuery.data?.hits ?? [];
  const err = searchQuery.error;

  return (
    <Card>
      <CardHeader title="跨文件搜索" />
      <CardBody className="space-y-3">
        <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="关键词…"
            aria-label="搜索关键词"
            className="min-w-40 flex-1 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-950"
          />
          <label className="text-xs text-slate-500">
            从
            <input
              type="date"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              aria-label="起始日期"
              className="ml-1 rounded-md border border-slate-300 bg-white px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-950"
            />
          </label>
          <label className="text-xs text-slate-500">
            到
            <input
              type="date"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              aria-label="结束日期"
              className="ml-1 rounded-md border border-slate-300 bg-white px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-950"
            />
          </label>
          <Button size="sm" type="submit">
            搜索
          </Button>
        </form>

        {err ? (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {err instanceof ApiError ? err.detail : '搜索失败'}
          </p>
        ) : null}

        {searchQuery.data?.partial ? (
          <p className="text-xs text-yellow-700 dark:text-yellow-400">
            搜索超时，仅显示已找到的部分结果。
          </p>
        ) : null}

        {submitted && !searchQuery.isLoading ? (
          <div className="max-h-80 overflow-auto rounded-md border border-slate-200 dark:border-slate-800">
            {hits.length === 0 ? (
              <p className="px-3 py-2 text-sm text-slate-400">没有命中</p>
            ) : (
              <table className="w-full text-left text-xs">
                <tbody>
                  {hits.map((h, i) => (
                    <tr
                      key={`${h.source}-${h.line}-${i}`}
                      onClick={() => onJump(h.source, h.line)}
                      className="cursor-pointer border-b border-slate-100 last:border-0 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/40"
                    >
                      <td className="whitespace-nowrap px-2 py-1 font-mono text-slate-500">
                        {h.source}:{h.line}
                      </td>
                      <td className="whitespace-nowrap px-2 py-1 text-slate-400">
                        {h.time ? formatTime(h.time) : '—'}
                      </td>
                      <td className="px-2 py-1 font-mono">{h.text}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        ) : null}
      </CardBody>
    </Card>
  );
}

// ---- 页面 ----------------------------------------------------------------

export function LogsPage() {
  const [current, setCurrent] = useState('latest.log');
  const [jumpToLine, setJumpToLine] = useState<number | null>(null);

  const sourcesQuery = useQuery({
    queryKey: ['logs', 'sources'],
    queryFn: ({ signal }) => logsApi.sources(signal),
    retry: false,
  });

  const handleJump = useCallback((source: string, line: number) => {
    setCurrent(source);
    // 用新引用触发查看器的跳转 effect，即便是同一行号也能再次定位。
    setJumpToLine(line);
  }, []);

  const handleSelect = useCallback((id: string) => {
    setCurrent(id);
    setJumpToLine(null);
  }, []);

  return (
    <div className="space-y-4">
      <PageHeader title="日志" subtitle="游戏日志、崩溃报告、系统日志与跨文件搜索" />

      {sourcesQuery.isError ? (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          无法加载日志源列表。
        </p>
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[16rem_1fr]">
        <Card>
          <CardBody>
            {sourcesQuery.isLoading ? (
              <p className="text-sm text-slate-400">加载中…</p>
            ) : sourcesQuery.data ? (
              <SourceList
                sources={sourcesQuery.data}
                current={current}
                onSelect={handleSelect}
              />
            ) : null}
          </CardBody>
        </Card>

        <div className="space-y-4">
          <Viewer source={current} jumpToLine={jumpToLine} />
          <SearchPanel onJump={handleJump} />
        </div>
      </div>

      {sourcesQuery.data ? (
        <p className="text-xs text-slate-400">
          共 {sourcesQuery.data.game.length} 个游戏日志、{sourcesQuery.data.crash.length} 份崩溃报告。
          最近更新 {formatDateTime(sourcesQuery.data.game[0]?.mtime ?? null)}。
        </p>
      ) : null}
    </div>
  );
}
