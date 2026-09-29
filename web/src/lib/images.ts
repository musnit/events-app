// Ask the image CDNs for thumbnails sized for where they appear instead of multi-megabyte originals.

export function imgUrl(url: string | null | undefined, px: number): string | undefined {
  if (!url) return undefined;
  try {
    const u = new URL(url);
    if ((u.hostname === "images.lumacdn.com" || u.hostname === "cdn.lu.ma") && !u.pathname.startsWith("/cdn-cgi/")) {
      return `https://${u.hostname}/cdn-cgi/image/format=auto,fit=cover,dpr=2,quality=75,width=${px},height=${px}${u.pathname}`;
    }
    if (u.hostname.endsWith("imgix.net")) {
      u.searchParams.set("w", String(px * 2));
      u.searchParams.set("h", String(px * 2));
      u.searchParams.set("fit", "crop");
      u.searchParams.set("auto", "format,compress");
      return u.toString();
    }
    return url;
  } catch {
    return url;
  }
}
