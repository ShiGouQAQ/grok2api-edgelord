import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRightLeft, Eraser } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { clearMihomoBlacklist, getMihomoStatus, switchMihomoNode, type MihomoMemberDTO } from "@/features/settings/settings-api";
import { ErrorState, LoadingState } from "@/shared/components/data-state";
import { cn } from "@/shared/lib/cn";

export function MihomoStatusPanel() {
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

  return (
    <section className="space-y-3">
      {query.isError ? <ErrorState message={query.error.message} onRetry={() => void query.refetch()} /> : null}
      {query.isPending ? <div className="min-h-28 rounded-lg bg-card p-4"><LoadingState /></div> : null}
      {query.data ? (
        <div className="min-h-28 rounded-lg bg-card p-4">
          {!query.data.enabled ? (
            <p className="text-xs text-muted-foreground">{t("settings.egress.mihomoDisabled")}</p>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <Tooltip><TooltipTrigger asChild><Button type="button" size="sm" variant="secondary" disabled={switchNode.isPending} onClick={() => switchNode.mutate()}>{switchNode.isPending ? <Spinner /> : <ArrowRightLeft />}{t("settings.egress.mihomoSwitch")}</Button></TooltipTrigger><TooltipContent>{t("settings.egress.mihomoSwitchHelp")}</TooltipContent></Tooltip>
                <Tooltip><TooltipTrigger asChild><Button type="button" size="sm" variant="secondary" disabled={clearBlacklist.isPending} onClick={() => clearBlacklist.mutate()}>{clearBlacklist.isPending ? <Spinner /> : <Eraser />}{t("settings.egress.mihomoClearBlacklist")}</Button></TooltipTrigger><TooltipContent>{t("settings.egress.mihomoClearBlacklistHelp")}</TooltipContent></Tooltip>
              </div>
              <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-2">
                <StatusValue label={t("settings.egress.mihomoCurrentNode")}>
                  <Badge variant="secondary" className="text-[10px]">{query.data.currentNode || "—"}</Badge>
                </StatusValue>
                <StatusValue label={t("settings.egress.mihomoSwitchCount")}>
                  <span className="text-xs tabular-nums text-foreground">{query.data.switchCount}</span>
                </StatusValue>
                <StatusValue label={t("settings.egress.mihomoReachable")}>
                  <Badge variant={query.data.reachable ? "secondary" : "destructive"} className={cn("text-[10px]", query.data.reachable && "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300")}>
                    {query.data.reachable ? t("settings.egress.healthy") : t("settings.egress.unhealthy")}
                  </Badge>
                </StatusValue>
                <StatusValue label={t("settings.egress.mihomoBannedNodes")}>
                  {query.data.bannedNodes.length > 0 ? (
                    <div className="flex flex-wrap gap-1">
                      {query.data.bannedNodes.map((node) => <Badge key={node} variant="outline" className="text-[10px] text-muted-foreground">{node}</Badge>)}
                    </div>
                  ) : <span className="text-xs text-muted-foreground">—</span>}
                </StatusValue>
                <StatusValue label={t("settings.web.mihomoAPIURL")}>
                  <span className="break-all font-mono text-xs text-muted-foreground">{query.data.apiUrl || "—"}</span>
                </StatusValue>
                <StatusValue label={t("settings.web.mihomoGroupName")}>
                  <span className="break-all font-mono text-xs text-muted-foreground">{query.data.groupName || "—"}</span>
                </StatusValue>
                {query.data.lastError ? (
                  <StatusValue label={t("settings.egress.mihomoLastError")} className="sm:col-span-2">
                    <span className="break-all text-xs text-destructive">{query.data.lastError}</span>
                  </StatusValue>
                ) : null}
              </dl>
              <MihomoMemberList members={query.data.members} testEnabled={query.data.testEnabled} testMembers={query.data.testMembers} />
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}

function StatusValue({ label, className, children }: { label: string; className?: string; children: React.ReactNode }) {
  return (
    <div className={cn("min-w-0 space-y-1", className)}>
      <dt className="text-[11px] font-medium text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

function MihomoMemberRow({ member }: { member: MihomoMemberDTO }) {
  const { t } = useTranslation();
  return (
    <TableRow className={cn(member.current && "bg-emerald-500/10", member.banned && "opacity-70")}>
      <TableCell className="text-xs">
        <span className={cn("font-medium", member.current && "text-emerald-700 dark:text-emerald-300", member.banned && "text-muted-foreground line-through")}>{member.name}</span>
      </TableCell>
      <TableCell className="text-xs text-muted-foreground">{member.provider || "—"}</TableCell>
      <TableCell className={cn("text-right text-xs tabular-nums", member.delayMs < 0 && "text-muted-foreground")}>{member.delayMs >= 0 ? `${member.delayMs}ms` : "—"}</TableCell>
      <TableCell className="text-xs">
        {member.current ? <Badge variant="secondary" className="bg-emerald-500/10 text-emerald-700 dark:text-emerald-300">{t("settings.egress.mihomoCurrent")}</Badge> : member.banned ? <Badge variant="destructive">{t("settings.egress.mihomoBanned")}</Badge> : null}
      </TableCell>
    </TableRow>
  );
}

function MihomoMemberTable({ title, members }: { title?: string; members: MihomoMemberDTO[] }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-1.5">
      {title ? <div className="text-[10px] font-medium text-muted-foreground">{title}</div> : null}
      {members.length > 0 ? (
        <div className="max-h-40 overflow-auto">
          <Table>
            <TableHeader><TableRow>
              <TableHead>{t("qualityGuard.node")}</TableHead>
              <TableHead>{t("qualityGuard.source")}</TableHead>
              <TableHead className="text-right">{t("settings.egress.mihomoLatency")}</TableHead>
              <TableHead>{t("qualityGuard.state")}</TableHead>
            </TableRow></TableHeader>
            <TableBody>
              {members.map((member) => <MihomoMemberRow key={member.name} member={member} />)}
            </TableBody>
          </Table>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">{t("settings.egress.mihomoNoData")}</p>
      )}
    </div>
  );
}

export function MihomoMemberList({ members, testEnabled, testMembers }: { members: MihomoMemberDTO[]; testEnabled: boolean; testMembers: MihomoMemberDTO[] }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-1.5">
      <div className="text-[10px] font-medium text-muted-foreground">{t("settings.egress.mihomoMembers")}</div>
      <MihomoMemberTable members={members} />
      {testEnabled && testMembers.length > 0 ? <MihomoMemberTable title={t("settings.egress.mihomoTestGroup")} members={testMembers} /> : null}
    </div>
  );
}
