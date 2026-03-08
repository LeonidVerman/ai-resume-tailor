// frontend/src/components/billing/BillingStatus.tsx
"use client";

import { CreditCard, CheckCircle, AlertCircle } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import type { BillingStatus as BillingStatusType } from "@/types/api";
import { formatDate } from "@/lib/utils";

interface BillingStatusProps {
  status: BillingStatusType;
  onUpgrade?: (plan: "starter" | "pro") => void;
  upgrading?: boolean;
}

const PLAN_LABELS: Record<string, string> = {
  free: "Free",
  starter: "Starter",
  pro: "Pro",
};

const PLAN_PRICES: Record<string, string> = {
  free: "$0/mo",
  starter: "$19/mo",
  pro: "$49/mo",
};

export function BillingStatusCard({ status, onUpgrade, upgrading }: BillingStatusProps) {
  const usagePercent = Math.min(
    100,
    (status.free_generations_used / status.free_generations_limit) * 100
  );
  const quotaFull = status.free_generations_used >= status.free_generations_limit;

  return (
    <div className="space-y-4">
      {/* Current plan */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <CreditCard className="h-5 w-5 text-indigo-600" />
              <h3 className="font-semibold text-gray-900">Current plan</h3>
            </div>
            <Badge variant={status.plan_type === "free" ? "default" : "purple"}>
              {PLAN_LABELS[status.plan_type] ?? status.plan_type}
            </Badge>
          </div>
        </CardHeader>
        <CardBody className="space-y-3">
          <div className="flex items-center justify-between text-sm">
            <span className="text-gray-600">Price</span>
            <span className="font-medium text-gray-900">
              {PLAN_PRICES[status.plan_type] ?? "—"}
            </span>
          </div>

          {status.subscription_status && (
            <div className="flex items-center justify-between text-sm">
              <span className="text-gray-600">Subscription status</span>
              <Badge
                variant={
                  status.subscription_status === "active"
                    ? "success"
                    : status.subscription_status === "trialing"
                    ? "info"
                    : "danger"
                }
              >
                {status.subscription_status}
              </Badge>
            </div>
          )}

          {status.current_period_end && (
            <div className="flex items-center justify-between text-sm">
              <span className="text-gray-600">Renews</span>
              <span className="text-gray-900">{formatDate(status.current_period_end)}</span>
            </div>
          )}

          {/* Free tier quota */}
          {status.plan_type === "free" && (
            <div className="pt-2">
              <div className="flex justify-between text-sm mb-1.5">
                <span className="text-gray-600">Free generations</span>
                <span className={quotaFull ? "text-red-600 font-medium" : "text-gray-900"}>
                  {status.free_generations_used} / {status.free_generations_limit}
                </span>
              </div>
              <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${quotaFull ? "bg-red-500" : "bg-indigo-500"}`}
                  style={{ width: `${usagePercent}%` }}
                />
              </div>
              {quotaFull && (
                <p className="text-xs text-red-600 mt-1.5 flex items-center gap-1">
                  <AlertCircle className="h-3 w-3" />
                  Quota exhausted. Upgrade to generate more.
                </p>
              )}
            </div>
          )}
        </CardBody>
      </Card>

      {/* Upgrade options — only shown on free plan */}
      {status.plan_type === "free" && (
        <div className="grid grid-cols-2 gap-3">
          {(["starter", "pro"] as const).map((plan) => (
            <Card key={plan} className="relative">
              {plan === "pro" && (
                <div className="absolute -top-2.5 left-1/2 -translate-x-1/2">
                  <Badge variant="purple">Most popular</Badge>
                </div>
              )}
              <CardBody className="space-y-3 py-5">
                <div>
                  <p className="font-semibold text-gray-900 capitalize">{plan}</p>
                  <p className="text-2xl font-bold text-gray-900 mt-0.5">
                    {PLAN_PRICES[plan]}
                  </p>
                </div>
                <ul className="space-y-1.5 text-sm text-gray-600">
                  {plan === "starter" ? (
                    <>
                      <li className="flex items-center gap-1.5"><CheckCircle className="h-3.5 w-3.5 text-green-500" />50 generations/mo</li>
                      <li className="flex items-center gap-1.5"><CheckCircle className="h-3.5 w-3.5 text-green-500" />DOCX exports</li>
                      <li className="flex items-center gap-1.5"><CheckCircle className="h-3.5 w-3.5 text-green-500" />Email support</li>
                    </>
                  ) : (
                    <>
                      <li className="flex items-center gap-1.5"><CheckCircle className="h-3.5 w-3.5 text-green-500" />Unlimited generations</li>
                      <li className="flex items-center gap-1.5"><CheckCircle className="h-3.5 w-3.5 text-green-500" />PDF + DOCX exports</li>
                      <li className="flex items-center gap-1.5"><CheckCircle className="h-3.5 w-3.5 text-green-500" />Priority support</li>
                    </>
                  )}
                </ul>
                <Button
                  variant="primary"
                  className="w-full"
                  loading={upgrading}
                  onClick={() => onUpgrade?.(plan)}
                >
                  Upgrade to {plan}
                </Button>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      {status.plan_type !== "free" && (
        <p className="text-sm text-gray-500 flex items-center gap-1.5">
          <CheckCircle className="h-4 w-4 text-green-500" />
          You&apos;re on the {PLAN_LABELS[status.plan_type]} plan. Manage your subscription via the customer portal (coming soon).
        </p>
      )}
    </div>
  );
}
