// StepTypeBadge —— 步骤类型徽章（19 步流水线五类节点的色彩区分）
const MAP: Record<string, { label: string; cls: string }> = {
  dispatch: {
    label: '工作步',
    cls: 'bg-accent/15 text-accent ring-1 ring-accent/30',
  },
  dispatch_group: {
    label: '并行组',
    cls: 'bg-accent/15 text-accent ring-1 ring-accent/40',
  },
  decision: {
    label: '拍板门',
    cls: 'bg-warning/15 text-warning ring-1 ring-warning/30',
  },
  wait: {
    label: '屏障',
    cls: 'bg-fg-3/15 text-fg-2 ring-1 ring-fg-3/30',
  },
  check: {
    label: '机器校验',
    cls: 'bg-error/15 text-error ring-1 ring-error/30',
  },
  done: {
    label: '收官',
    cls: 'bg-success/15 text-success ring-1 ring-success/30',
  },
}

export function StepTypeBadge({ type }: { type: string }) {
  const cfg = MAP[type] ?? {
    label: type,
    cls: 'bg-fg-3/15 text-fg-2 ring-1 ring-fg-3/30',
  }
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${cfg.cls}`}
    >
      {cfg.label}
    </span>
  )
}
