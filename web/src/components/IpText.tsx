import { Link } from 'react-router-dom';
import { ipDisplay, UNTRUSTED_IP_TITLE } from '@/lib/ipDisplay';
import { cn } from '@/lib/cn';

/**
 * Renders an IP value with trust-aware styling. Untrusted (pre-PROXY) IPs are
 * gray + italic with the "启用真实 IP 前的记录" tooltip. When `link` is true and
 * the IP is present, it links to the IP profile page.
 */
export function IpText({
  ip,
  trusted,
  link = false,
  className,
}: {
  ip: string | null;
  trusted: boolean;
  link?: boolean;
  className?: string;
}) {
  const d = ipDisplay(ip, trusted);
  const title = d.untrusted ? UNTRUSTED_IP_TITLE : undefined;

  if (ip !== null && link) {
    return (
      <Link
        to={`/ips/${encodeURIComponent(ip)}`}
        title={title}
        className={cn(d.className, 'underline-offset-2 hover:underline', className)}
      >
        {d.text}
      </Link>
    );
  }
  return (
    <span title={title} className={cn(d.className, className)}>
      {d.text}
    </span>
  );
}
