import { cn } from '@/lib/cn';

/**
 * 卡片里的长列表 / 长表格统一用固定上限高度 + 内部滚动，避免一张卡片把整页撑长。
 * 上限同时受视口限制（手机上不超过屏幕的一半多），表头在滚动时吸顶（见 index.css 的 .scroll-box）。
 */
export type ScrollSize = 'sm' | 'md' | 'lg';

const HEIGHT: Record<ScrollSize, string> = {
  sm: 'max-h-[min(18rem,50vh)]',
  md: 'max-h-[min(28rem,60vh)]',
  lg: 'max-h-[min(40rem,70vh)]',
};

export function scrollBox(size: ScrollSize, extra?: string): string {
  return cn('scroll-box overflow-auto overscroll-contain', HEIGHT[size], extra);
}
