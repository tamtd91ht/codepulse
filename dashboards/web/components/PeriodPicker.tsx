"use client";

import { useState } from "react";

import { DateRangePicker } from "@/components/DateRangePicker";
import { Pager, Segmented } from "@/components/ui";
import { periodLabel } from "@/lib/format";
import type { useCycleScope } from "@/lib/timeScope";

type Choice = "today" | "week" | "month" | "range";

/**
 * The period header of the week / month / range screens (Tổng quan, Luồng làm việc, a dự án, a
 * team): Hôm nay · Tuần · Tháng · Khoảng ngày, then the week / month pager or the date-range
 * picker. "Hôm nay" is the range today..today, i.e. from 00:00 to now (docs/specs/10); it is
 * shown as chosen whenever the shared period is exactly that range.
 */
export function PeriodPicker({
  time,
  rangeMin,
  rangeMaxDays,
}: {
  time: ReturnType<typeof useCycleScope>;
  /** Earliest day a range may start (the screen's API limit). */
  rangeMin?: string;
  /** Longest range, in days. */
  rangeMaxDays?: number;
}) {
  // "Khoảng ngày" clicked while today is chosen: show the picker on today's range.
  const [custom, setCustom] = useState(false);
  if (!time) return null;
  const { period, start, range, today, latest } = time;
  const isToday = period === "range" && range.from === today && range.to === today;
  const value: Choice = isToday && !custom ? "today" : period;

  const choose = (next: Choice) => {
    setCustom(next === "range");
    if (next === "today") time.setRange({ from: today, to: today });
    else if (next !== period) time.changePeriod(next);
  };

  return (
    <>
      <Segmented<Choice>
        label="Chu kỳ"
        value={value}
        onChange={choose}
        options={[
          { value: "today", label: "Hôm nay" },
          { value: "week", label: "Tuần" },
          { value: "month", label: "Tháng" },
          { value: "range", label: "Khoảng ngày" },
        ]}
      />
      {value === "range" && (
        <DateRangePicker
          label="Thời gian"
          value={range}
          min={rangeMin}
          max={today}
          maxSpan={rangeMaxDays}
          onChange={(r) => {
            setCustom(true);
            time.setRange(r);
          }}
        />
      )}
      {(value === "week" || value === "month") && (
        <Pager
          label={periodLabel(value, start)}
          onPrev={() => time.step(-1)}
          onNext={() => time.step(1)}
          nextDisabled={start >= latest}
          prevDisabled={false}
        />
      )}
    </>
  );
}
