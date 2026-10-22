import type { Metadata } from "next";
import type { ReactNode } from "react";

import { Nav } from "@/components/Nav";

import "./globals.css";

export const metadata: Metadata = {
  title: "CodePulse",
  description: "Chất lượng code theo team và từng thành viên",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="vi">
      <body>
        <Nav />
        <main>
          {children}
          {/* docs/specs/11: scores are a coaching signal, stated on the dashboard. */}
          <p className="notice">
            Điểm là tín hiệu coaching, không phải căn cứ duy nhất để đánh giá hiệu suất. Mỗi người
            xem được toàn bộ dữ liệu về mình và có thể khiếu nại (dispute) từng finding.
          </p>
        </main>
      </body>
    </html>
  );
}
