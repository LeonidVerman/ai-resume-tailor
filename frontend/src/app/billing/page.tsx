// frontend/src/app/billing/page.tsx
"use client";

import { useState, useEffect } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { Spinner } from "@/components/ui/Spinner";
import { BillingStatusCard } from "@/components/billing/BillingStatus";
import { billing, ApiError } from "@/lib/api";
import type { BillingStatus } from "@/types/api";

export default function BillingPage() {
  const [status, setStatus] = useState<BillingStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [upgrading, setUpgrading] = useState(false);
  const [upgradeError, setUpgradeError] = useState<string | null>(null);

  useEffect(() => {
    billing
      .status()
      .then(setStatus)
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Failed to load billing status."))
      .finally(() => setLoading(false));
  }, []);

  async function handleUpgrade(plan: "starter" | "pro") {
    setUpgrading(true);
    setUpgradeError(null);
    try {
      const origin = window.location.origin;
      const result = await billing.createCheckout({
        plan_type: plan,
        success_url: `${origin}/billing?upgraded=1`,
        cancel_url: `${origin}/billing`,
      });
      window.location.href = result.checkout_url;
    } catch (e) {
      setUpgradeError(
        e instanceof ApiError
          ? e.detail
          : "Failed to create checkout session. Stripe may not be configured."
      );
      setUpgrading(false);
    }
  }

  return (
    <AppShell>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Billing</h1>
        <p className="text-gray-500 mt-1">Manage your plan and usage.</p>
      </div>

      {loading ? (
        <div className="py-12 flex justify-center">
          <Spinner size="lg" label="Loading billing status..." />
        </div>
      ) : error ? (
        <div className="bg-red-50 border border-red-200 rounded-xl p-6 text-red-700">
          <p className="font-medium">Could not load billing status</p>
          <p className="text-sm mt-1">{error}</p>
        </div>
      ) : status ? (
        <div className="max-w-2xl">
          <BillingStatusCard
            status={status}
            onUpgrade={handleUpgrade}
            upgrading={upgrading}
          />
          {upgradeError && (
            <p className="mt-3 text-sm text-red-600">✗ {upgradeError}</p>
          )}
        </div>
      ) : null}
    </AppShell>
  );
}
