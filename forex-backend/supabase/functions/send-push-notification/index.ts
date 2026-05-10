// Supabase Edge Function: send-push-notification
// Runtime: Deno (TypeScript)
// Deploy: supabase functions deploy send-push-notification
//
// This function:
//   1. Receives notification payload from the FastAPI backend
//   2. Reads the single push subscription from the DB (your device)
//   3. Signs and sends the Web Push notification using VAPID
//
// Environment variables (set in Supabase Dashboard → Edge Functions → Secrets):
//   VAPID_PUBLIC_KEY   — your VAPID public key
//   VAPID_PRIVATE_KEY  — your VAPID private key (keep secret)
//   VAPID_SUBJECT      — mailto:your@email.com
//   SUPABASE_URL       — auto-injected by Supabase
//   SUPABASE_SERVICE_ROLE_KEY — auto-injected by Supabase

import { serve } from "https://deno.land/std@0.208.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.39.3";
import {
  applicationServerKey,
  generateVAPIDKeys,
  PushSubscription,
  sendWebPush,
  WebPushInfoError,
} from "https://esm.sh/web-push@3.6.7";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

// ─── VAPID Config ─────────────────────────────────────────────────────────────
const VAPID_PUBLIC_KEY = Deno.env.get("VAPID_PUBLIC_KEY") ?? "";
const VAPID_PRIVATE_KEY = Deno.env.get("VAPID_PRIVATE_KEY") ?? "";
const VAPID_SUBJECT = Deno.env.get("VAPID_SUBJECT") ?? "mailto:admin@forex-bot.com";

// ─── Supabase client ──────────────────────────────────────────────────────────
const supabase = createClient(
  Deno.env.get("SUPABASE_URL") ?? "",
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? ""
);

// ─── Notification payload builder ─────────────────────────────────────────────
function buildNotificationPayload(body: {
  type: string;
  title: string;
  body: string;
  data: Record<string, unknown>;
  icon?: string;
  badge?: string;
  timestamp?: string;
}): string {
  const payload = {
    notification: {
      title: body.title,
      body: body.body,
      icon: body.icon ?? "/icon-192.png",
      badge: body.badge ?? "/badge-72.png",
      vibrate: [200, 100, 200],
      requireInteraction: isHighPriority(body.type),
      tag: body.type, // Replace previous notification of same type
      renotify: body.type === "TRADE_OPENED" || body.type === "RISK_ALERT",
      timestamp: body.timestamp ? new Date(body.timestamp).getTime() : Date.now(),
      data: {
        ...body.data,
        type: body.type,
        url: getNotificationUrl(body.type, body.data),
      },
      actions: getNotificationActions(body.type),
    },
  };
  return JSON.stringify(payload);
}

function isHighPriority(type: string): boolean {
  return ["RISK_ALERT", "DRAWDOWN_ALERT", "MARGIN_WARNING", "TRADE_OPENED"].includes(type);
}

function getNotificationUrl(type: string, data: Record<string, unknown>): string {
  switch (type) {
    case "TRADE_OPENED":
    case "TRADE_CLOSED":
      return `/trades/${data.trade_id ?? ""}`;
    case "DAILY_REPORT":
    case "WEEKLY_REPORT":
    case "MONTHLY_REPORT":
    case "ANNUAL_REPORT":
      return "/dashboard/reports";
    case "RISK_ALERT":
    case "DRAWDOWN_ALERT":
    case "MARGIN_WARNING":
      return "/dashboard/risk";
    default:
      return "/dashboard";
  }
}

function getNotificationActions(type: string): Array<{ action: string; title: string }> {
  switch (type) {
    case "TRADE_OPENED":
      return [{ action: "view_trade", title: "Voir le trade" }];
    case "TRADE_CLOSED":
      return [{ action: "view_history", title: "Historique" }];
    case "RISK_ALERT":
    case "DRAWDOWN_ALERT":
      return [
        { action: "view_risk", title: "Voir risques" },
        { action: "close_trades", title: "Gérer trades" },
      ];
    default:
      return [{ action: "view_dashboard", title: "Dashboard" }];
  }
}

// ─── Log notification to DB ───────────────────────────────────────────────────
async function logNotification(
  type: string,
  title: string,
  body: string,
  success: boolean,
  error?: string
): Promise<void> {
  try {
    await supabase.from("notification_logs").insert({
      type,
      title,
      body,
      sent: success,
      error: error ?? null,
      sent_at: new Date().toISOString(),
    });
  } catch (e) {
    console.error("Failed to log notification:", e);
  }
}

// ─── Main handler ─────────────────────────────────────────────────────────────
serve(async (req: Request) => {
  // CORS preflight
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  // Only POST allowed
  if (req.method !== "POST") {
    return new Response(JSON.stringify({ error: "Method not allowed" }), {
      status: 405,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }

  // Parse body
  let notificationBody: {
    type: string;
    title: string;
    body: string;
    data: Record<string, unknown>;
    icon?: string;
    badge?: string;
    timestamp?: string;
  };

  try {
    notificationBody = await req.json();
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON body" }), {
      status: 400,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }

  // Validate required fields
  if (!notificationBody.type || !notificationBody.title || !notificationBody.body) {
    return new Response(JSON.stringify({ error: "Missing required fields: type, title, body" }), {
      status: 400,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }

  // Validate VAPID keys are configured
  if (!VAPID_PUBLIC_KEY || !VAPID_PRIVATE_KEY) {
    console.error("VAPID keys not configured");
    return new Response(JSON.stringify({ error: "Push notifications not configured" }), {
      status: 500,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }

  // Fetch the single subscription from DB
  const { data: subscriptions, error: dbError } = await supabase
    .from("push_subscriptions")
    .select("*")
    .eq("active", true)
    .limit(1);

  if (dbError) {
    console.error("DB error fetching subscription:", dbError);
    return new Response(JSON.stringify({ error: "Database error" }), {
      status: 500,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }

  if (!subscriptions || subscriptions.length === 0) {
    console.warn("No active push subscription found");
    await logNotification(
      notificationBody.type,
      notificationBody.title,
      notificationBody.body,
      false,
      "No subscription registered"
    );
    return new Response(
      JSON.stringify({ success: false, message: "No subscription registered" }),
      { status: 200, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }

  const sub = subscriptions[0];
  const pushSubscription: PushSubscription = {
    endpoint: sub.endpoint,
    keys: {
      auth: sub.auth_key,
      p256dh: sub.p256dh_key,
    },
  };

  // Build and send push notification
  const payload = buildNotificationPayload(notificationBody);

  try {
    await sendWebPush(
      pushSubscription,
      payload,
      {
        vapidDetails: {
          subject: VAPID_SUBJECT,
          publicKey: VAPID_PUBLIC_KEY,
          privateKey: VAPID_PRIVATE_KEY,
        },
        TTL: 86400, // 24 hours
        urgency: isHighPriority(notificationBody.type) ? "high" : "normal",
      }
    );

    await logNotification(
      notificationBody.type,
      notificationBody.title,
      notificationBody.body,
      true
    );

    console.log(`✅ Push sent: [${notificationBody.type}] ${notificationBody.title}`);

    return new Response(JSON.stringify({ success: true }), {
      status: 200,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  } catch (err) {
    const errorMsg = err instanceof Error ? err.message : String(err);
    console.error("Push send error:", errorMsg);

    // If subscription expired/invalid → deactivate it
    if (err instanceof WebPushInfoError || errorMsg.includes("410") || errorMsg.includes("404")) {
      await supabase
        .from("push_subscriptions")
        .update({ active: false, deactivated_at: new Date().toISOString() })
        .eq("id", sub.id);
      console.warn("Subscription deactivated (expired/invalid)");
    }

    await logNotification(
      notificationBody.type,
      notificationBody.title,
      notificationBody.body,
      false,
      errorMsg
    );

    return new Response(JSON.stringify({ success: false, error: errorMsg }), {
      status: 500,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }
});
