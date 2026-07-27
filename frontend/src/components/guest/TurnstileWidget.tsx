// frontend/src/components/guest/TurnstileWidget.tsx
//
// Cloudflare Turnstile widget for the guest /try flow (issue #155).
// Site key comes from NEXT_PUBLIC_TURNSTILE_SITE_KEY. When the key is not
// set in a development build, a stub is rendered that immediately passes a
// dummy token (the dev backend skips real verification in that mode).

"use client";

import { useEffect, useRef, useState } from "react";

interface TurnstileOptions {
  sitekey: string;
  callback: (token: string) => void;
  "expired-callback"?: () => void;
  "error-callback"?: () => void;
}

declare global {
  interface Window {
    turnstile?: {
      render: (el: HTMLElement, opts: TurnstileOptions) => string;
      remove: (widgetId: string) => void;
    };
  }
}

const SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY;
const SCRIPT_SRC =
  "https://challenges.cloudflare.com/turnstile/api.js?render=explicit";
// Explicit local-testing bypass: renders the stub even in production
// builds.  Never set this in a real deployment.
const DEV_BYPASS = process.env.NEXT_PUBLIC_TURNSTILE_DEV_BYPASS === "1";
const DEV_STUB =
  DEV_BYPASS || (!SITE_KEY && process.env.NODE_ENV === "development");
export const DEV_STUB_TOKEN = "dev-stub-turnstile-token";

interface TurnstileWidgetProps {
  /** Called with the verification token, or null when it expires/errors. */
  onToken: (token: string | null) => void;
}

export function TurnstileWidget({ onToken }: TurnstileWidgetProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const onTokenRef = useRef(onToken);
  onTokenRef.current = onToken;
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => {
    if (DEV_STUB) {
      onTokenRef.current(DEV_STUB_TOKEN);
      return;
    }
    if (!SITE_KEY) return;

    let widgetId: string | null = null;
    let cancelled = false;

    function renderWidget() {
      if (cancelled || widgetId || !containerRef.current || !window.turnstile) {
        return;
      }
      widgetId = window.turnstile.render(containerRef.current, {
        sitekey: SITE_KEY as string,
        callback: (token) => onTokenRef.current(token),
        "expired-callback": () => onTokenRef.current(null),
        "error-callback": () => onTokenRef.current(null),
      });
    }

    if (window.turnstile) {
      renderWidget();
    } else {
      let script = document.querySelector<HTMLScriptElement>(
        `script[src="${SCRIPT_SRC}"]`
      );
      if (!script) {
        script = document.createElement("script");
        script.src = SCRIPT_SRC;
        script.async = true;
        document.head.appendChild(script);
      }
      script.addEventListener("load", renderWidget);
      script.addEventListener("error", () => {
        if (!cancelled) setLoadFailed(true);
      });
    }

    // Fallback poll: closes the race where the script finished loading
    // between the window.turnstile check and addEventListener, and surfaces
    // a visible error if the script never arrives (blocked/offline).
    const startedAt = Date.now();
    const poll = window.setInterval(() => {
      if (cancelled || widgetId) {
        window.clearInterval(poll);
        return;
      }
      if (window.turnstile) {
        renderWidget();
        window.clearInterval(poll);
      } else if (Date.now() - startedAt > 15000) {
        setLoadFailed(true);
        window.clearInterval(poll);
      }
    }, 500);

    return () => {
      cancelled = true;
      window.clearInterval(poll);
      if (widgetId && window.turnstile) window.turnstile.remove(widgetId);
    };
  }, []);

  if (loadFailed) {
    return (
      <p className="text-sm text-red-600">
        Could not load the verification widget (challenges.cloudflare.com may
        be blocked by your network or an extension). Please allow it and
        reload.
      </p>
    );
  }

  if (DEV_STUB) {
    return (
      <p className="text-xs text-gray-400 border border-dashed border-gray-200 rounded-lg px-3 py-2">
        Human verification is stubbed in development (NEXT_PUBLIC_TURNSTILE_SITE_KEY not set).
      </p>
    );
  }
  if (!SITE_KEY) {
    return (
      <p className="text-sm text-red-600">
        Human verification is not configured. Please try again later.
      </p>
    );
  }
  return <div ref={containerRef} />;
}
