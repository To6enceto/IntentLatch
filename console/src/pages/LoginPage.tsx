import { useEffect, useRef, useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router";
import { useAuth } from "../auth";
import { BrandMark } from "../components/BrandMark";
import { Icon } from "../components/Icon";
import { ThemeToggle, type ThemeControlProps } from "../components/ThemeToggle";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { ApiError, errorMessage } from "../lib/api";

type FieldErrors = { username?: string; password?: string };

function LoginForm({ expired }: { expired: boolean }) {
  const { signIn } = useAuth();
  const usernameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const [showPassword, setShowPassword] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [feedback, setFeedback] = useState(expired ? "Your session has ended. Sign in again." : "");
  const [failed, setFailed] = useState(false);
  const [pending, setPending] = useState(false);
  const [attempt, setAttempt] = useState(0);

  function clearFeedback(field: keyof FieldErrors) {
    setErrors((previous) => ({ ...previous, [field]: undefined }));
    setFeedback("");
    setFailed(false);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setAttempt((previous) => previous + 1);
    const nextErrors: FieldErrors = {};
    if (!usernameRef.current?.value.trim()) nextErrors.username = "Enter a username.";
    if (!passwordRef.current?.value) nextErrors.password = "Enter a password.";
    setErrors(nextErrors);
    if (nextErrors.username || nextErrors.password) {
      setFeedback("Check the required fields.");
      setFailed(true);
      (nextErrors.username ? usernameRef : passwordRef).current?.focus();
      return;
    }
    setPending(true);
    setFeedback("");
    try {
      // On success the session changes and LoginPage navigates away.
      await signIn(usernameRef.current!.value.trim(), passwordRef.current!.value);
    } catch (error) {
      setPending(false);
      setFailed(true);
      setFeedback(error instanceof ApiError && error.code === "invalid_request"
        ? "The username or password is incorrect."
        : errorMessage(error));
      passwordRef.current!.value = "";
      passwordRef.current!.focus();
    }
  }

  return (
    <form className="login-form" onSubmit={submit} noValidate>
      <div className="login-field">
        <label htmlFor="username">Username</label>
        <Input id="username" name="username" autoComplete="username" required ref={usernameRef} aria-invalid={!!errors.username} aria-describedby={errors.username ? "username-error" : undefined} onChange={() => clearFeedback("username")} />
        {errors.username && <p id="username-error" className="field-error">{errors.username}</p>}
      </div>
      <div className="login-field">
        <label htmlFor="password">Password</label>
        <div className="password-input">
          <Input id="password" name="password" type={showPassword ? "text" : "password"} autoComplete="current-password" required ref={passwordRef} className="pr-11" aria-invalid={!!errors.password} aria-describedby={errors.password ? "password-error" : undefined} onChange={() => clearFeedback("password")} />
          <Button variant="ghost" size="icon" className="password-toggle" aria-label={showPassword ? "Hide password" : "Show password"} aria-pressed={showPassword} aria-controls="password" onClick={() => setShowPassword(!showPassword)}>
            <Icon name={showPassword ? "eyeOff" : "eye"} />
          </Button>
        </div>
        {errors.password && <p id="password-error" className="field-error">{errors.password}</p>}
      </div>
      <Button type="submit" className="login-submit" disabled={pending}>{pending ? "Signing in…" : <>Sign in<Icon name="login" /></>}</Button>
      <div role="status" aria-live="polite" aria-atomic="true" className={feedback ? `login-feedback${failed ? " is-error" : ""}` : undefined}>{feedback && <span key={attempt}>{feedback}</span>}</div>
    </form>
  );
}

export function LoginPage(themeControl: ThemeControlProps) {
  const { session } = useAuth();
  const from = (useLocation().state as { from?: string } | null)?.from;
  useEffect(() => { document.title = "Sign in | IntentLatch Console"; }, []);

  if (session.status === "signed-in") {
    return <Navigate to={from && from.startsWith("/") && !from.startsWith("/login") ? from : "/metrics"} replace />;
  }

  return (
    <div className="login-page">
      <a href="#login-content" className="skip-link">Skip to sign-in form</a>
      <header className="login-controls">
        <ThemeToggle {...themeControl} />
      </header>
      <main id="login-content" className="login-main" tabIndex={-1}>
        <div className="login-container">
          <section className="login-card" aria-labelledby="login-title">
            <div className="login-body">
              <BrandMark large />
              <div className="login-heading">
                <h1 id="login-title">IntentLatch Console</h1>
                <p>Sign in to the management console</p>
              </div>
              <LoginForm expired={session.status === "signed-out" && session.expired} />
            </div>
            <footer className="login-footer">No default accounts. Ask an administrator for access.</footer>
          </section>
          <p className="login-caption">IntentLatch · AI control layer</p>
        </div>
      </main>
    </div>
  );
}
