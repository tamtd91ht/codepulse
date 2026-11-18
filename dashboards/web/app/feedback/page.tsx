"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { MyFeedback } from "@/components/Feedback";
import { FeedbackBoard } from "@/components/FeedbackBoard";
import { Icon } from "@/components/icons";
import { Alert, Loading, PageHeader } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import type { FeedbackMeta } from "@/lib/feedback";

/*
 * Phản ánh & Đề xuất (docs/specs/32). "Của tôi": the form and one's own reports. "Tổng hợp"
 * (feedback.manage): the tree, the list with picks, the report and its triage, the export.
 * ?from= is the page the menu was opened from (it suggests the area); ?tab=board opens the board.
 */

type Tab = "mine" | "board";

export default function FeedbackPage() {
  return (
    <Suspense fallback={<Loading rows={6} />}>
      <FeedbackScreen />
    </Suspense>
  );
}

function FeedbackScreen() {
  const params = useSearchParams();
  const from = params.get("from");
  const { data: meta, error } = useApi<FeedbackMeta>(`/feedback/meta${qs({ page: from })}`);
  const [tab, setTab] = useState<Tab>(params.get("tab") === "board" ? "board" : "mine");
  useEffect(() => {
    if (meta && !meta.can_manage && tab === "board") setTab("mine");
  }, [meta, tab]);
  const pick = (next: Tab) => {
    setTab(next);
    const p = new URLSearchParams(window.location.search);
    p.set("tab", next);
    if (next === "mine") p.delete("item");
    window.history.replaceState(null, "", `${window.location.pathname}?${p}`);
  };

  return (
    <>
      <PageHeader
        title="Phản ánh & Đề xuất"
        subtitle="Góp ý, báo lỗi, đề xuất tính năng cho CodePulse. AI phân loại và tóm tắt; người quản lý chọn và giao cho agent nâng cấp."
      />
      {error ? (
        <Alert tone="error">{error.message}</Alert>
      ) : !meta ? (
        <Loading rows={6} />
      ) : (
        <>
          {meta.can_manage && (
            <div className="tabs" role="tablist" aria-label="Phản ánh">
              <button type="button" role="tab" aria-selected={tab === "mine"} className={tab === "mine" ? "active" : undefined} onClick={() => pick("mine")}>
                <Icon name="user" size={14} /> Của tôi
              </button>
              <button type="button" role="tab" aria-selected={tab === "board"} className={tab === "board" ? "active" : undefined} onClick={() => pick("board")}>
                <Icon name="board" size={14} /> Tổng hợp
              </button>
            </div>
          )}
          {tab === "board" && meta.can_manage ? <FeedbackBoard meta={meta} /> : <MyFeedback meta={meta} from={from} />}
        </>
      )}
    </>
  );
}
