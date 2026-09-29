import { useState } from "react";
import { imgUrl } from "../lib/images.ts";

function initials(name: string): string {
  const words = name.replace(/[^\p{L}\p{N} ]/gu, " ").split(/\s+/).filter(Boolean);
  return (words.length > 1 ? words[0][0] + words[1][0] : (words[0] || "?").slice(0, 2)).toUpperCase();
}

/** A round (or rounded-square) picture that falls back to initials when missing or broken. */
export function Avatar({ src, name, size = 28, square = false }: { src: string | null | undefined; name: string; size?: number; square?: boolean }) {
  const [broken, setBroken] = useState(false);
  const url = imgUrl(src, size);
  const style = { width: size, height: size, fontSize: Math.max(9, Math.round(size * 0.38)) };
  const className = `avatar${square ? " square" : ""}`;
  if (!url || broken) {
    return <span className={`${className} initials`} style={style} aria-hidden="true">{initials(name)}</span>;
  }
  return (
    <img className={className} src={url} alt="" width={size} height={size} style={style} loading="lazy" decoding="async"
      referrerPolicy="no-referrer" onError={() => setBroken(true)} />
  );
}
