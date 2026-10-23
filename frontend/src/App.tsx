import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";

import { getDemoRole, getHealth, setDemoRole } from "./lib/api";
import type { DemoRole } from "./lib/api";
import Matching from "./pages/Matching";
import Observability from "./pages/Observability";
import Precheck from "./pages/Precheck";
import Rules from "./pages/Rules";
import RunCenter from "./pages/RunCenter";
import Supplier from "./pages/Supplier";

const NAV = [
  { to: "/", label: "Run 中心", end: true },
  { to: "/precheck", label: "M1 预检" },
  { to: "/supplier", label: "M2 供应商" },
  { to: "/matching", label: "M3 三单" },
  { to: "/rules", label: "规则库" },
  { to: "/observability", label: "可观测" },
];

const ROLES: DemoRole[] = ["requester", "buyer", "finance", "admin"];

export default function App() {
  const [role, setRole] = useState<DemoRole>(getDemoRole());
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth, retry: false, refetchInterval: 30_000 });

  const onRoleChange = (next: DemoRole) => {
    setDemoRole(next);
    setRole(next);
  };

  const dbDown = health.data ? !health.data.db : false;

  return (
    <div className="flex min-h-screen">
      <aside className="w-52 shrink-0 border-r border-[var(--border-default)] bg-[var(--bg-surface)]">
        <div className="flex h-14 items-center gap-2 border-b border-[var(--border-default)] px-4">
          <span className="h-6 w-6 rounded-md bg-[var(--brand-primary)]" />
          <span className="text-sm font-semibold">Procurement AI</span>
        </div>
        <nav className="flex flex-col gap-1 p-2">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `rounded-md px-3 py-2 text-[13px] ${
                  isActive
                    ? "bg-[var(--bg-subtle)] font-medium text-[var(--brand-primary)]"
                    : "text-[var(--text-secondary)] hover:bg-[var(--bg-subtle)]"
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between border-b border-[var(--border-default)] bg-[var(--bg-surface)] px-6">
          <div className="text-[13px] text-[var(--text-secondary)]">
            {health.data ? (
              <span>
                环境 <b>{health.data.env}</b> · 版本 <b>{health.data.version}</b>
              </span>
            ) : (
              <span>健康检查未就绪</span>
            )}
          </div>
          <label className="flex items-center gap-2 text-[13px] text-[var(--text-secondary)]">
            角色
            <select
              className="rounded-md border border-[var(--border-default)] bg-white px-2 py-1"
              value={role}
              onChange={(event) => onRoleChange(event.target.value as DemoRole)}
            >
              {ROLES.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
        </header>

        {dbDown && (
          <div className="border-b border-amber-200 bg-amber-50 px-6 py-2 text-[13px] text-[var(--semantic-warning)]">
            数据库未连接：先执行 <code>docker compose up -d db</code> 与 <code>make migrate</code>，再新建运行。
            当前页面仍可浏览（健康检查已降级）。
          </div>
        )}

        <main className="min-w-0 flex-1 p-6">
          <Routes>
            <Route path="/" element={<RunCenter />} />
            <Route path="/precheck" element={<Precheck />} />
            <Route path="/supplier" element={<Supplier />} />
            <Route path="/matching" element={<Matching />} />
            <Route path="/rules" element={<Rules />} />
            <Route path="/observability" element={<Observability />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
