/**
 * Main entry point for the DearLive Games API Provider
 * Vercel serverless function entry point
 */
import { handleRequest } from './router';

export default async function handler(req: any, res: any) {
  const method = req.method;
  const path = req.url;
  const query = req.query;
  const headers = req.headers;
  const body = req.body ? JSON.stringify(req.body) : '';

  const result = await handleRequest(method, path, query, headers, body);

  res.status(result.status);
  Object.entries(result.headers).forEach(([key, value]) => {
    res.setHeader(key, value);
  });
  res.send(result.body);
}

export { handleRequest } from './router';
