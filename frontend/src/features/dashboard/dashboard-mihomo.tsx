import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRightLeft, Eraser } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { DashboardPanel } from "@/features/dashboard/dashboard-panel";
import { clearMihomoBlacklist, getMihomoStatus, switchMihomoNode } from "@/features/settings/settings-api";
import { cn } from "@/shared/lib/cn";

// DashboardMihomo 以紧凑小卡展示完整的 Mihomo 出口状态与快捷操作；未启用或未配置时隐藏。
export function DashboardMihomo() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["egress-mihomo-status"],
    queryFn: getMihomoStatus,
    refetchInterval: 2_000,
    refetchIntervalInBackground: false,
  });

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ["egress-mihomo-status"] });

  const switchNode = useMutation({
    mutationFn: switchMihomoNode,
    onSuccess: (value) => { invalidate(); toast.success(t("settings.egress.mihomoSwitched", { node: value.node })); },
    onError: (error) => { invalidate(); toast.error(error.message); },
  });
  const clearBlacklist = useMutation({
    mutationFn: clearMihomoBlacklist,
    onSuccess: (value) => { invalidate(); toast.success(t("settings.egress.mihomoBlacklistCleared", { count: value.cleared })); },
    onError: (error) => { invalidate(); toast.error(error.message); },
  });

  if (query.isPending || query.isError || !query.data?.enabled) return null;
  const data = query.data;

  return (
    <DashboardPanel id="dashboard-mihomo" title={t("settings.egress.mihomo")}>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 lg:grid-cols-4">
        <StatusValue label={t("settings.egress.mihomoCurrentNode")}>
          <Badge variant="secondary" className="max-w-full truncate text-[10px]">{data.currentNode || "—"}</Badge>
        </StatusValue>
        <StatusValue label={t("settings.egress.mihomoSwitchCount")}>
          <span className="text-[11px] tabular-nums text-foreground">{data.switchCount}</span>
        </StatusValue>
        <StatusValue label={t("settings.egress.mihomoReachable")}>
          <Badge variant={data.reachable ? "secondary" : "destructive"} className={cn("text-[10px]", data.reachable && "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300")}>
            {data.reachable ? t("settings.egress.healthy") : t("settings.egress.unhealthy")}
          </Badge>
        </StatusValue>
        <StatusValue label={t("settings.egress.mihomoBannedNodes")}>
          {data.bannedNodes.length > 0 ? (
            <div className="flex max-h-10 flex-wrap gap-1 overflow-y-auto">
              {data.bannedNodes.map((node) => <Badge key={node} variant="outline" className="text-[10px] text-muted-foreground">{node}</Badge>)}
            </div>
          ) : <span className="text-[11px] text-muted-foreground">—</span>}
        </StatusValue>
        <StatusValue label={t("settings.web.mihomoAPIURL")} className="col-span-2">
          <span className="block truncate font-mono text-[11px] text-muted-foreground">{data.apiUrl || "—"}</span>
        </StatusValue>
        <StatusValue label={t("settings.web.mihomoGroupName")} className="col-span-2">
          <span className="block truncate font-mono text-[11px] text-muted-foreground">{data.groupName || "—"}</span>
        </StatusValue>
        {data.lastError ? (
          <StatusValue label={t("settings.egress.mihomoLastError")} className="col-span-2">
            <span className="block truncate text-[11px] text-destructive">{data.lastError}</span>
          </StatusValue>
        ) : null}
      </dl>
      <div className="mt-3 flex items-center gap-1.5">
        <Tooltip><TooltipTrigger asChild><Button type="button" size="sm" variant="secondary" disabled={switchNode.isPending} onClick={() => switchNode.mutate()}>{switchNode.isPending ? <Spinner /> : <ArrowRightLeft />}{t("settings.egress.mihomoSwitch")}</Button></TooltipTrigger><TooltipContent>{t("settings.egress.mihomoSwitchHelp")}</TooltipContent></Tooltip>
        <Tooltip><TooltipTrigger asChild><Button type="button" size="sm" variant="secondary" disabled={clearBlacklist.isPending} onClick={() => clearBlacklist.mutate()}>{clearBlacklist.isPending ? <Spinner /> : <Eraser />}{t("settings.egress.mihomoClearBlacklist")}</Button></TooltipTrigger><TooltipContent>{t("settings.egress.mihomoClearBlacklistHelp")}</TooltipContent></Tooltip>
      </div>
    </DashboardPanel>
  );
}

function StatusValue({ label, className, children }: { label: string; className?: string; children: React.ReactNode }) {
  return (
    <div className={cn("min-w-0 space-y-0.5", className)}>
      <dt className="text-[10px] font-medium text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}
