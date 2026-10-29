import type { Metadata } from "next";

import { Icon } from "@/components/icons";

export const metadata: Metadata = { title: "Đăng nhập" };

export default function LoginPage() {
  return (
    <div className="login-wrap">
      <div className="card login-card">
        <span className="brand-mark">
          <img src="/icon.svg" alt="" width={40} height={40} />
        </span>
        <h1>CodePulse</h1>
        <p className="lead">Chất lượng code theo team và từng thành viên. Đăng nhập bằng tài khoản GitLab của công ty.</p>
        {/* A full navigation (not fetch): the API redirects to GitLab and back. */}
        <a href="/api/v1/auth/login" className="button primary ink">
          <Icon name="gitlab" size={16} />
          Đăng nhập với GitLab
        </a>
        <p className="foot">
          Chưa rõ CodePulse đo gì và ai xem được gì? <a href="/guide">Đọc hướng dẫn</a>
        </p>
        {/* docs/specs/11: the coaching notice sits at the foot of every page, this one included. */}
        <p className="foot">Điểm là tín hiệu coaching, không phải căn cứ duy nhất để đánh giá hiệu suất.</p>
      </div>
    </div>
  );
}
