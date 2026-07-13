import type { FC } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BadgeCheck, ShieldAlert, ShieldCheck, ShieldQuestion, ShieldX } from 'lucide-react';
import { MenuBar } from '@/components/MenuBar';
import { MobileBottomNav } from '@/components/MobileBottomNav';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useApi } from '@/contexts/ApiContext';
import type { SkillRegistryItem, SkillReputationBand } from '@/types/api';
import { cn } from '@/lib/utils';
import { use$ } from '@legendapp/state/react';

const bandStyles: Record<SkillReputationBand, string> = {
  excellent: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
  good: 'border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300',
  neutral: 'border-muted-foreground/30 bg-muted text-muted-foreground',
  low: 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300',
  blocked: 'border-destructive/40 bg-destructive/10 text-destructive',
};

const BandIcon: Record<SkillReputationBand, typeof ShieldQuestion> = {
  excellent: ShieldCheck,
  good: BadgeCheck,
  neutral: ShieldQuestion,
  low: ShieldAlert,
  blocked: ShieldX,
};

const ReputationBadge: FC<{ skill: SkillRegistryItem }> = ({ skill }) => {
  const { reputation } = skill;
  const Icon = BandIcon[reputation.band];
  const score = reputation.score === null ? null : Math.round(reputation.score * 100);

  return (
    <Badge
      variant="outline"
      className={cn(
        'h-6 gap-1 rounded-md px-2 text-[11px] font-medium',
        bandStyles[reputation.band]
      )}
      title={score === null ? reputation.band_label : `${reputation.band_label} (${score})`}
    >
      <Icon className="h-3 w-3" />
      <span>{reputation.band_label}</span>
      {score !== null && <span className="tabular-nums">{score}</span>}
    </Badge>
  );
};

const SkillCard: FC<{ skill: SkillRegistryItem }> = ({ skill }) => (
  <Card className="rounded-lg">
    <CardHeader className="space-y-2 pb-3">
      <div className="flex items-start justify-between gap-3">
        <CardTitle className="min-w-0 truncate text-base">{skill.name}</CardTitle>
        <ReputationBadge skill={skill} />
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>{skill.install_count.toLocaleString()} installs</span>
        {skill.category && <span>{skill.category}</span>}
      </div>
    </CardHeader>
    <CardContent>
      <p className="line-clamp-3 min-h-[3.75rem] text-sm text-muted-foreground">
        {skill.description || 'No description provided.'}
      </p>
    </CardContent>
  </Card>
);

const Skills: FC = () => {
  const { api, isConnected$ } = useApi();
  const isConnected = use$(isConnected$);
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['skills', api.baseUrl],
    queryFn: () => api.getSkills(),
    enabled: isConnected,
    staleTime: 60 * 1000,
  });

  const skills = data?.skills ?? [];

  return (
    <div className="flex h-dvh flex-col bg-background">
      <MenuBar />
      <main className="flex-1 overflow-y-auto pb-16 md:pb-0">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-4 py-5 sm:px-6">
          <div className="flex items-end justify-between gap-3">
            <div>
              <h1 className="text-xl font-semibold tracking-normal">Skills</h1>
              <p className="text-sm text-muted-foreground">
                {skills.length} discoverable {skills.length === 1 ? 'skill' : 'skills'}
              </p>
            </div>
          </div>

          {!isConnected && (
            <div className="rounded-lg border bg-muted/40 p-4 text-sm text-muted-foreground">
              Connect to a gptme server to browse skills.
            </div>
          )}
          {isConnected && isLoading && (
            <div className="rounded-lg border bg-muted/40 p-4 text-sm text-muted-foreground">
              Loading skills...
            </div>
          )}
          {isError && (
            <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
              {error instanceof Error ? error.message : 'Failed to load skills'}
            </div>
          )}

          {skills.length > 0 && (
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {skills.map((skill) => (
                <SkillCard key={skill.name} skill={skill} />
              ))}
            </div>
          )}
        </div>
      </main>
      <MobileBottomNav />
    </div>
  );
};

export default Skills;
