// frontend/src/components/guest/TurnstileWidget.tsx
//
// Cloudflare Turnstile widget for the guest /try flow (issue #155).
// Site key comes from NEXT_PUBLIC_TURNSTILE_SITE_KEY. When the key is not
// set in a development build, a stub is rendered that immediately passes a
// dummy token (the dev backend skips real verification in that mode).

"use client";

import { useEffect, useRef } from "react";

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
const DEV_STUB = !SITE_KEY && process.env.NODE_ENV === "development";
export const DEV_STUB_TOKEN = "dev-stub-turnstile-token";

interface TurnstileWidgetProps {
  /** Called with the verification token, or null when it expires/errors. */
  onToken: (token: string | null) => void;
}

export function TurnstileWidget({ onToken }: TurnstileWidgetProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const onTokenRef = useRef(onToken);
  onTokenRef.current = onToken;

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
    }

    return () => {
      cancelled = true;
      if (widgetId && window.turnstile) window.turnstile.remove(widgetId);
    };
  }, []);

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
