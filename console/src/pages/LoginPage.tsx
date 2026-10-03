import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { BrandMark } from "../components/BrandMark";
import { Icon } from "../components/Icon";
import { ThemeToggle, type ThemeControlProps } from "../components/ThemeToggle";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";

type FieldErrors = { username?: string; password?: string };

function LoginForm() {
  const usernameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const [showPassword, setShowPassword] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [feedback, setFeedback] = useState("");
  const [attempt, setAttempt] = useState(0);

  function clearFeedback(field: keyof FieldErrors) {
    setErrors((previous) => ({ ...previous, [field]: undefined }));
    setFeedback("");
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAttempt((previous) => previous + 1);
    const nextErrors: FieldErrors = {};
    if (!usernameRef.current?.value.trim()) nextErrors.username = "Enter a username.";
    if (!passwordRef.current?.value) nextErrors.password = "Enter a password.";
    setErrors(nextErrors);
    if (nextErrors.username || nextErrors.password) {
      setFeedback("Check the required fields.");
      (nextErrors.username ? usernameRef : passwordRef).current?.focus();
      return;
    }
    setFeedback("Sign-in is unavailable.");
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
      <Button type="submit" className="login-submit">Sign in<Icon name="login" /></Button>
      <div role="status" aria-live="polite" aria-atomic="true" className={feedback ? "login-feedback" : undefined}>{feedback && <span key={attempt}>{feedback}</span>}</div>
    </form>
  );
}

export function LoginPage(themeControl: ThemeControlProps) {
  useEffect(() => { document.title = "Sign in | IntentLatch Console"; }, []);

  return (
    <div className="login-page">
      <a href="#login-content" className="skip-link">Skip to sign-in form</a>
      <header className="login-controls">
        <Link to="/metrics" className="back-link"><Icon name="collapse" />Back to console</Link>
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
              <LoginForm />
            </div>
            <footer className="login-footer">No default accounts. Ask an administrator for access.</footer>
          </section>
          <p className="login-caption">IntentLatch · AI control layer</p>
        </div>
      </main>
    </div>
  );
}
