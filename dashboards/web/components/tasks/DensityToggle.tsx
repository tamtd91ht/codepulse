"use client";

import { useEffect, useRef, useState } from "react";

import { Segmented } from "@/components/ui";

/*
 * "Thoáng / Gọn" (docs/specs/31 §19.8): puts `tk-dense` on the List / Board wrapper it sits in,
 * remembered per view in this browser. The CSS lives at the end of globals.css.
 */

type Density = "comfy" | "dense";
const WRAPPER = ".tk-list, .tk-board-wrap";

function read(key: string): Density {
  try {
    return window.localStorage.getItem(key) === "dense" ? "dense" : "comfy";
  } catch {
    return "comfy";
  }
}

export function DensityToggle({ view }: { view: "list" | "board" }) {
  const key = `tk-density-${view}`;
  const box = useRef<HTMLDivElement>(null);
  const [density, setDensity] = useState<Density>("comfy");

  useEffect(() => setDensity(read(key)), [key]);
  useEffect(() => {
    const wrap = box.current?.closest(WRAPPER);
    wrap?.classList.toggle("tk-dense", density === "dense");
    return () => wrap?.classList.remove("tk-dense");
  }, [density]);

  function change(next: Density) {
    setDensity(next);
    try {
      window.localStorage.setItem(key, next);
    } catch {
      /* private window: the choice lasts for this page only */
    }
  }

  return (
    <div className="rq-field nl-density" ref={box}>
      <span className="rq-label">Mật độ</span>
      <Segmented<Density>
        label="Mật độ hiển thị"
        value={density}
        onChange={change}
        options={[
          { value: "comfy", label: "Thoáng" },
          { value: "dense", label: "Gọn" },
        ]}
      />
    </div>
  );
}
