import { type FC, useMemo } from 'react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { toISODate } from '@/utils/time';

interface ActivityCalendarProps {
  /** Map of ISO date string (YYYY-MM-DD) to conversation count */
  activityData: Map<string, number>;
  /** Start date for the calendar (aligned to its Sunday). If omitted, computed from weeks. */
  startDate?: Date;
  /** End date for the calendar (defaults to today) */
  endDate?: Date;
  /** Number of weeks to show when startDate is not provided (default: 52) */
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

export const ActivityCalendar: FC<ActivityCalendarProps> = ({
  activityData,
  startDate,
  endDate,
  weeks = 52,
  onDayClick,
  selectedDate,
}) => {
  const { cells, monthLabels, maxCount, todayStr } = useMemo(() => {
    const today = new Date();
    today.setHours(12, 0, 0, 0);
    const todayStr = toISODate(today);

    const end = endDate ? new Date(endDate) : new Date(today);
    end.setHours(12, 0, 0, 0);
    const endStr = toISODate(end);

    // Align start to Sunday
    let startDay: Date;
    if (startDate) {
      startDay = new Date(startDate);
      startDay.setHours(12, 0, 0, 0);
      // Align back to Sunday
      startDay.setDate(startDay.getDate() - startDay.getDay());
    } else {
      startDay = new Date(end);
      startDay.setDate(end.getDate() - end.getDay() - (weeks - 1) * 7);
    }

    const cells: { date: string; count: number; col: number; row: number }[] = [];
    const monthLabels: { label: string; col: number }[] = [];

    let maxCount = 0;
    let lastMonth = -1;
    const current = new Date(startDay);
    let dayIndex = 0;

    while (toISODate(current) <= endStr) {
      const col = Math.floor(dayIndex / 7);
      const row = dayIndex % 7;

      const dateStr = toISODate(current);
      const count = activityData.get(dateStr) || 0;
      if (count > maxCount) maxCount = count;

      cells.push({ date: dateStr, count, col, row });

      if (current.getMonth() !== lastMonth && row === 0) {
        lastMonth = current.getMonth();
        monthLabels.push({ label: MONTH_NAMES[current.getMonth()], col });
      }

      current.setDate(current.getDate() + 1);
      dayIndex++;
    }

    if (monthLabels.length === 0 || monthLabels[0].col > 1) {
      const firstDate = new Date(startDay);
      monthLabels.unshift({ label: MONTH_NAMES[firstDate.getMonth()], col: 0 });
    }

    return { cells, monthLabels, maxCount, todayStr };
  }, [activityData, startDate, endDate, weeks]);

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
          const isToday = date === todayStr;
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
                  className={`${colorClass} cursor-pointer transition-opacity hover:opacity-80 ${isSelected ? 'stroke-foreground stroke-[2]' : isToday ? 'stroke-foreground stroke-[1.5]' : ''}`}
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
                  {isToday ? ' (today)' : ''}
                </p>
              </TooltipContent>
            </Tooltip>
          );
        })}
      </svg>

      {/* Legend */}
      <div className="mt-2 flex items-center justify-end gap-1 text-xs text-muted-foreground">
        <span>Less</span>
        {[
          'fill-muted',
          'fill-emerald-200 dark:fill-emerald-900',
          'fill-emerald-400 dark:fill-emerald-700',
          'fill-emerald-500 dark:fill-emerald-500',
          'fill-emerald-700 dark:fill-emerald-400',
        ].map((cls, i) => (
          <svg key={i} width={CELL_SIZE} height={CELL_SIZE}>
            <rect width={CELL_SIZE} height={CELL_SIZE} rx={2} className={cls} />
          </svg>
        ))}
        <span>More</span>
      </div>
    </div>
  );
};
