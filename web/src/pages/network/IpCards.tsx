import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { networkApi, type IpRow, type IpTag, type NetWindow } from '@/api/network';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { formatDateTime } from '@/lib/format';
import { TAG_LABEL } from './constants';
import { TagBadges } from './TopologyCards';
import { scrollBox } from '@/lib/scroll';

// ---- Top IP 表 -------------------------------------------------------------
export function IpTable({
  window,
  observe,
  onBan,
  onPardon,
}: {
  window: NetWindow;
  observe: boolean;
  onBan: (ip: string) => void;
  onPardon: (ip: string) => void;
}) {
  const [q, setQ] = useState('');
  const [tag, setTag] = useState<IpTag | ''>('');
  const query = useQuery({
    queryKey: ['network', 'ips', window, q, tag],
    queryFn: ({ signal }) =>
      networkApi.ips({ window, limit: 100, ...(q ? { q } : {}), ...(tag ? { tag } : {}) }, signal),
    retry: false,
  });
  const items: IpRow[] = query.data?.items ?? [];

  return (
    <Card>
      <CardHeader title={`Top IP（${query.data?.total ?? 0}）`} />
      <CardBody className="space-y-3">
        <div className="flex flex-wrap gap-2">
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="按 IP 或玩家名过滤…"
            aria-label="过滤 IP"
            className="min-w-40 flex-1 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-950"
          />
          <select
            value={tag}
            onChange={(e) => setTag(e.target.value as IpTag | '')}
            aria-label="按标签过滤"
            className="rounded-md border border-slate-300 bg-white px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-950"
          >
            <option value="">全部标签</option>
            {(Object.keys(TAG_LABEL) as IpTag[]).map((t) => (
              <option key={t} value={t}>
                {TAG_LABEL[t]}
              </option>
            ))}
          </select>
        </div>

        <div className={scrollBox('lg', 'overflow-x-auto')} tabIndex={0} role="region" aria-label="来源 IP 列表">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-xs text-slate-400">
                <th className="py-1">IP</th>
                <th className="py-1">连接</th>
                <th className="py-1">标签</th>
                <th className="py-1">玩家</th>
                <th className="py-1">最近出现</th>
                <th className="py-1 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {query.isLoading ? (
                <tr>
                  <td colSpan={6} className="py-3 text-slate-400">
                    加载中…
                  </td>
                </tr>
              ) : items.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-3 text-slate-400">
                    无匹配 IP
                  </td>
                </tr>
              ) : (
                items.map((it) => (
                  <tr key={it.ip} className="border-t border-slate-100 dark:border-slate-800">
                    <td className="py-1 font-mono">
                      <Link to={`/ips/${it.ip}`} className="text-brand hover:underline">
                        {it.ip}
                      </Link>
                    </td>
                    <td className="py-1 tabular-nums">{it.conns}</td>
                    <td className="py-1">
                      <TagBadges tags={it.tags} />
                    </td>
                    <td className="py-1">
                      {it.players.map((p, i) => (
                        <span key={p}>
                          {i > 0 ? ', ' : ''}
                          <Link to={`/players/${p}`} className="text-brand hover:underline">
                            {p}
                          </Link>
                        </span>
                      ))}
                    </td>
                    <td className="py-1 text-xs text-slate-500">{formatDateTime(it.last_seen)}</td>
                    <td className="py-1 text-right">
                      {it.banned ? (
                        <Button
                          size="sm"
                          variant="secondary"
                          disabled={observe}
                          onClick={() => onPardon(it.ip)}
                        >
                          解封
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          variant="danger"
                          disabled={observe}
                          onClick={() => onBan(it.ip)}
                        >
                          封禁
                        </Button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </CardBody>
    </Card>
  );
}

// ---- SSH 来源提示 ----------------------------------------------------------
export function SshCard() {
  const query = useQuery({
    queryKey: ['network', 'ssh'],
    queryFn: ({ signal }) => networkApi.ssh(signal),
    retry: false,
  });
  const items = query.data?.items ?? [];
  if (items.length === 0) return null;
  return (
    <Card>
      <CardHeader title={<span className="text-red-600 dark:text-red-400">⚠ 最近 24h SSH 来源</span>} />
      <CardBody>
        <ul className={scrollBox('sm', 'space-y-1 text-sm')} tabIndex={0}>
          {items.map((s) => (
            <li key={s.ip} className="flex items-center justify-between gap-2">
              <Link to={`/ips/${s.ip}`} className="font-mono text-brand hover:underline">
                {s.ip}
              </Link>
              <span className="text-xs text-slate-500">
                {s.conns} 次 · 最近 {formatDateTime(s.last_seen)}
              </span>
            </li>
          ))}
        </ul>
      </CardBody>
    </Card>
  );
}

// ---- 扫描器卡片 ------------------------------------------------------------
export function ScannerCard({
  window,
  observe,
  onBanAll,
}: {
  window: NetWindow;
  observe: boolean;
  onBanAll: () => void;
}) {
  const query = useQuery({
    queryKey: ['network', 'scanners', window],
    queryFn: ({ signal }) => networkApi.scanners(window, signal),
    retry: false,
  });
  const data = query.data;
  return (
    <Card>
      <CardHeader
        title={`扫描器（${data?.count ?? 0}）`}
        action={
          <Button
            size="sm"
            variant="danger"
            disabled={observe || !data || data.count === 0}
            onClick={onBanAll}
          >
            一键封禁
          </Button>
        }
      />
      <CardBody>
        {query.isLoading ? (
          <p className="text-sm text-slate-400">加载中…</p>
        ) : data && data.items.length > 0 ? (
          <ul className={scrollBox('md', 'space-y-1 text-sm')} tabIndex={0}>
            {data.items.slice(0, 20).map((it) => (
              <li key={it.ip} className="flex items-center justify-between gap-2">
                <Link to={`/ips/${it.ip}`} className="font-mono text-brand hover:underline">
                  {it.ip}
                </Link>
                <span className="text-xs text-slate-500">{it.conns} 次</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-400">暂无扫描器候选</p>
        )}
      </CardBody>
    </Card>
  );
}

// ---- 封禁列表 --------------------------------------------------------------
export function BansCard({ observe, onPardon }: { observe: boolean; onPardon: (ip: string) => void }) {
  const query = useQuery({
    queryKey: ['network', 'bans'],
    queryFn: ({ signal }) => networkApi.bans(signal),
    retry: false,
  });
  const items = query.data?.items ?? [];
  return (
    <Card>
      <CardHeader title={`封禁列表（${items.length}）`} />
      <CardBody>
        {query.isLoading ? (
          <p className="text-sm text-slate-400">加载中…</p>
        ) : items.length === 0 ? (
          <p className="text-sm text-slate-400">暂无封禁</p>
        ) : (
          <div className={scrollBox('md', 'overflow-x-auto')} tabIndex={0} role="region" aria-label="封禁列表">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-xs text-slate-400">
                <th className="py-1">IP</th>
                <th className="py-1">原因</th>
                <th className="py-1">来源</th>
                <th className="py-1 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((b) => (
                <tr key={b.ip} className="border-t border-slate-100 dark:border-slate-800">
                  <td className="py-1 font-mono">
                    <Link to={`/ips/${b.ip}`} className="text-brand hover:underline">
                      {b.ip}
                    </Link>
                  </td>
                  <td className="py-1 text-xs">{b.reason ?? '—'}</td>
                  <td className="py-1 text-xs text-slate-500">{b.source ?? '—'}</td>
                  <td className="py-1 text-right">
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={observe}
                      onClick={() => onPardon(b.ip)}
                    >
                      解封
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
