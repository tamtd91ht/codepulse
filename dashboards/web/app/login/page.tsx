export default function LoginPage() {
  return (
    <div className="card" style={{ maxWidth: 420, margin: "64px auto", textAlign: "center" }}>
      <h1>CodePulse</h1>
      <p className="muted">Đăng nhập bằng tài khoản GitLab của công ty.</p>
      {/* A full navigation (not fetch): the API redirects to GitLab and back. */}
      <a href="/api/v1/auth/login">
        <button className="primary">Đăng nhập với GitLab</button>
      </a>
    </div>
  );
}
