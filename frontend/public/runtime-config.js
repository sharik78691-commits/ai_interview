/*
 * Runtime configuration for the AI Interview Assistant frontend.
 *
 * This file is served as a static asset and loaded BEFORE the Angular bundle,
 * so values can be changed on the host (Vercel) without rebuilding the app.
 *
 * WS_BASE_URL
 *   Backend origin used for the live-interview WebSocket. Static hosts cannot
 *   proxy a WebSocket upgrade to an external origin, so in production this MUST
 *   point at the backend host, e.g. "wss://ai-interview-1-309j.onrender.com".
 *   Leave it empty for local development: the Angular dev-server proxy then
 *   forwards the same-origin "/ws" path to the backend.
 */
window.WS_BASE_URL = window.WS_BASE_URL || 'wss://ai-interview-1-309j.onrender.com';
