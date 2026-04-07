// frontend/src/components/billing/BillingStatus.tsx
"use client";

import { CreditCard, CheckCircle, AlertCircle, ShoppingCart, Settings } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import type { BillingStatus as BillingStatusType } from "@/types/api";
import { formatDate } from "@/lib/utils";

interface BillingStatusProps {
  status: BillingStatusType;
  onUpgrade?: (plan: "starter" | "pro") => void;
  onBuyCredits?: () => void;
  onManagePortal?: () => void;
  upgrading?: boolean;
  buyingCredits?: boolean;
  openingPortal?: boolean;
}

const PLAN_LABELS: Record<string, string> = {
  free: "Free",
  starter: "Starter",
  pro: "Pro",
};

const PLAN_PRICES: Record<string, string> = {
  free: "$0/mo",
  starter: "$7.99/mo",
  pro: "$19.99/mo",
};

export function BillingStatusCard({
  status,
  onUpgrade,
  onBuyCredits,
  onManagePortal,
  upgrading,
  buyingCredits,
  openingPortal,
}: BillingStatusProps) {
  const usagePercent = Math.min(
    100,
    status.monthly_limit > 0
      ? (status.monthly_used / status.monthly_limit) * 100
      : 0
  );
  const quotaFull = status.monthly_used >= status.monthly_limit && status.extra_credits === 0;
  const nearQuota = !quotaFull && status.monthly_used >= status.monthly_limit;

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

          {/* Monthly usage bar */}
          <div className="pt-2">
            <div className="flex justify-between text-sm mb-1.5">
              <span className="text-gray-600">Monthly generations</span>
              <span className={quotaFull ? "text-red-600 font-medium" : "text-gray-900"}>
                {status.monthly_used} / {status.monthly_limit}
              </span>
            </div>
            <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${
                  quotaFull ? "bg-red-500" : usagePercent >= 80 ? "bg-amber-500" : "bg-indigo-500"
                }`}
                style={{ width: `${usagePercent}%` }}
              />
            </div>
            {quotaFull && (
              <p className="text-xs text-red-600 mt-1.5 flex items-center gap-1">
                <AlertCircle className="h-3 w-3" />
                Monthly quota reached. Use extra credits or upgrade.
              </p>
            )}
            {nearQuota && status.extra_credits > 0 && (
              <p className="text-xs text-amber-600 mt-1.5">
                Monthly quota reached — using extra credits ({status.extra_credits} remaining).
              </p>
            )}
          </div>

          {/* Extra credits */}
          {status.extra_credits > 0 && (
            <div className="flex items-center justify-between text-sm pt-1">
              <span className="text-gray-600">Extra credits</span>
              <Badge variant="success">{status.extra_credits} remaining</Badge>
            </div>
          )}

          {/* Manage portal for paid plans */}
          {status.plan_type !== "free" && onManagePortal && (
            <Button
              variant="secondary"
              size="sm"
              className="w-full mt-2"
              loading={openingPortal}
              onClick={onManagePortal}
            >
              <Settings className="h-4 w-4" />
              Manage billing
            </Button>
          )}
        </CardBody>
      </Card>

      {/* Buy credits */}
      <Card>
        <CardBody className="flex items-center justify-between gap-4">
          <div>
            <p className="font-medium text-gray-900 text-sm">Extra generations</p>
            <p className="text-xs text-gray-500 mt-0.5">10 extra generations — never expire</p>
          </div>
          <Button
            variant="secondary"
            size="sm"
            loading={buyingCredits}
            onClick={onBuyCredits}
          >
            <ShoppingCart className="h-4 w-4" />
            Buy 10 for $4.99
          </Button>
        </CardBody>
      </Card>

      {/* Upgrade options */}
      {status.plan_type !== "pro" && (
        <div className={status.plan_type === "free" ? "grid grid-cols-2 gap-3" : "grid grid-cols-1 gap-3"}>
          {(["starter", "pro"] as const)
            .filter((plan) => {
              if (status.plan_type === "free") return true;       // free → show both
              if (status.plan_type === "starter") return plan === "pro"; // starter → show only pro
              return false;
            })
            .map((plan) => (
              <Card key={plan} className="relative">
                {plan === "starter" && status.plan_type === "free" && (
                  <div className="absolute -top-2.5 left-1/2 -translate-x-1/2">
                    <Badge variant="info">Most popular</Badge>
                  </div>
                )}
                {plan === "pro" && status.plan_type === "starter" && (
                  <div className="absolute -top-2.5 left-1/2 -translate-x-1/2">
                    <Badge variant="purple">Upgrade available</Badge>
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
                        <li className="flex items-center gap-1.5"><CheckCircle className="h-3.5 w-3.5 text-green-500" />40 generations/mo</li>
                        <li className="flex items-center gap-1.5"><CheckCircle className="h-3.5 w-3.5 text-green-500" />DOCX exports</li>
                        <li className="flex items-center gap-1.5"><CheckCircle className="h-3.5 w-3.5 text-green-500" />Email support</li>
                      </>
                    ) : (
                      <>
                        <li className="flex items-center gap-1.5"><CheckCircle className="h-3.5 w-3.5 text-green-500" />200 generations/mo</li>
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
                    {status.plan_type === "starter" && plan === "pro"
                      ? "Upgrade to Pro"
                      : `Upgrade to ${plan}`}
                  </Button>
                  {status.plan_type === "starter" && plan === "pro" && (
                    <p className="text-xs text-gray-500 text-center">
                      Prorated charge for the remainder of your billing cycle.
                    </p>
                  )}
                </CardBody>
              </Card>
            ))}
        </div>
      )}
    </div>
  );
}
