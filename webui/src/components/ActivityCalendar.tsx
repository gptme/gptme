import { type FC, useMemo } from 'react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

interface ActivityCalendarProps {
  /** Map of ISO date string (YYYY-MM-DD) to conversation count */
  activityData: Map<string, number>;
  /** Number of weeks to show (default: 52) */
  weeks?: number;
  /** Called when a day cell is clicked */
  onDayClick?: (date: string) => void;
  /** Currently selected date */
  selectedDate?: string | null;
}

const CELL_SIZE = 12;
const CELL_GAP = 2;
const CELL_STEP = CELL_SIZE + CELL_GAP;
const DAYS_IN_WEEK = 7;
const MONTH_LABEL_HEIGHT = 16;
const DAY_LABEL_WIDTH = 28;

const DAY_LABELS = ['', 'Mon', '', 'Wed', '', 'Fri', ''];
const MONTH_NAMES = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

function getColorClass(count: number, max: number): string {
  if (count === 0) return 'fill-muted';
  const ratio = count / Math.max(max, 1);
  if (ratio <= 0.25) return 'fill-emerald-200 dark:fill-emerald-900';
  if (ratio <= 0.5) return 'fill-emerald-400 dark:fill-emerald-700';
  if (ratio <= 0.75) return 'fill-emerald-500 dark:fill-emerald-500';
  return 'fill-emerald-700 dark:fill-emerald-400';
}

function toISODate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export const ActivityCalendar: FC<ActivityCalendarProps> = ({
  activityData,
  weeks = 52,
  onDayClick,
  selectedDate,
}) => {
  const { cells, monthLabels, maxCount } = useMemo(() => {
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    // Find the start date: go back `weeks` weeks from end of this week
    const endDay = new Date(today);
    const startDay = new Date(today);
    startDay.setDate(startDay.getDate() - (weeks * 7 - 1) - startDay.getDay());

    const cells: { date: string; count: number; col: number; row: number }[] = [];
    const monthLabels: { label: string; col: number }[] = [];

    let maxCount = 0;
    let lastMonth = -1;
    const current = new Date(startDay);

    while (current <= endDay) {
      const dayOfWeek = current.getDay(); // 0=Sun
      const daysSinceStart = Math.floor(
        (current.getTime() - startDay.getTime()) / (1000 * 60 * 60 * 24)
      );
      const col = Math.floor(daysSinceStart / 7);
      const row = dayOfWeek;

      const dateStr = toISODate(current);
      const count = activityData.get(dateStr) || 0;
      if (count > maxCount) maxCount = count;

      cells.push({ date: dateStr, count, col, row });

      // Track month labels
      if (current.getMonth() !== lastMonth) {
        lastMonth = current.getMonth();
        if (col > 0 || row === 0) {
          monthLabels.push({ label: MONTH_NAMES[current.getMonth()], col });
        }
      }

      current.setDate(current.getDate() + 1);
    }

    return { cells, monthLabels, maxCount };
  }, [activityData, weeks]);

  const totalCols = cells.length > 0 ? Math.max(...cells.map((c) => c.col)) + 1 : 0;
  const svgWidth = DAY_LABEL_WIDTH + totalCols * CELL_STEP;
  const svgHeight = MONTH_LABEL_HEIGHT + DAYS_IN_WEEK * CELL_STEP;

  return (
    <div className="w-full overflow-x-auto">
      <svg width={svgWidth} height={svgHeight} className="block">
        {/* Month labels */}
        {monthLabels.map(({ label, col }, i) => (
          <text
            key={`month-${i}`}
            x={DAY_LABEL_WIDTH + col * CELL_STEP}
            y={MONTH_LABEL_HEIGHT - 4}
            className="fill-muted-foreground text-[10px]"
          >
            {label}
          </text>
        ))}

        {/* Day labels */}
        {DAY_LABELS.map(
          (label, i) =>
            label && (
              <text
                key={`day-${i}`}
                x={0}
                y={MONTH_LABEL_HEIGHT + i * CELL_STEP + CELL_SIZE - 2}
                className="fill-muted-foreground text-[10px]"
              >
                {label}
              </text>
            )
        )}

        {/* Day cells */}
        {cells.map(({ date, count, col, row }) => {
          const x = DAY_LABEL_WIDTH + col * CELL_STEP;
          const y = MONTH_LABEL_HEIGHT + row * CELL_STEP;
          const isSelected = selectedDate === date;
          const colorClass = getColorClass(count, maxCount);

          return (
            <Tooltip key={date}>
              <TooltipTrigger asChild>
                <rect
                  x={x}
                  y={y}
                  width={CELL_SIZE}
                  height={CELL_SIZE}
                  rx={2}
                  className={`${colorClass} cursor-pointer transition-opacity hover:opacity-80 ${isSelected ? 'stroke-foreground stroke-[1.5]' : ''}`}
                  onClick={() => onDayClick?.(date)}
                />
              </TooltipTrigger>
              <TooltipContent>
                <p className="font-medium">
                  {count} conversation{count !== 1 ? 's' : ''} on{' '}
                  {new Date(date + 'T00:00:00').toLocaleDateString(undefined, {
                    weekday: 'short',
                    month: 'short',
                    day: 'numeric',
                    year: 'numeric',
                  })}
                </p>
              </TooltipContent>
            </Tooltip>
          );
        })}
      </svg>

      {/* Legend */}
      <div className="mt-2 flex items-center justify-end gap-1 text-xs text-muted-foreground">
        <span>Less</span>
        <svg width={CELL_SIZE} height={CELL_SIZE}>
          <rect width={CELL_SIZE} height={CELL_SIZE} rx={2} className="fill-muted" />
        </svg>
        <svg width={CELL_SIZE} height={CELL_SIZE}>
          <rect
            width={CELL_SIZE}
            height={CELL_SIZE}
            rx={2}
            className="fill-emerald-200 dark:fill-emerald-900"
          />
        </svg>
        <svg width={CELL_SIZE} height={CELL_SIZE}>
          <rect
            width={CELL_SIZE}
            height={CELL_SIZE}
            rx={2}
            className="fill-emerald-400 dark:fill-emerald-700"
          />
        </svg>
        <svg width={CELL_SIZE} height={CELL_SIZE}>
          <rect
            width={CELL_SIZE}
            height={CELL_SIZE}
            rx={2}
            className="fill-emerald-500 dark:fill-emerald-500"
          />
        </svg>
        <svg width={CELL_SIZE} height={CELL_SIZE}>
          <rect
            width={CELL_SIZE}
            height={CELL_SIZE}
            rx={2}
            className="fill-emerald-700 dark:fill-emerald-400"
          />
        </svg>
        <span>More</span>
      </div>
    </div>
  );
};
