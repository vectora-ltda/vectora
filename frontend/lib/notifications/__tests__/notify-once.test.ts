// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  deliverLocalNotification,
  notificationKey,
  notifyOnce,
  wasDelivered,
} from "../notify-once";

beforeEach(() => {
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

describe("notifyOnce", () => {
  it("entrega uma vez e trata a repetição da mesma chave como duplicada", () => {
    const deliver = vi.fn(() => true);
    const key = notificationKey("t1", "turn-1", "done");

    expect(notifyOnce(key, deliver)).toBe("delivered");
    expect(notifyOnce(key, deliver)).toBe("duplicate");
    expect(notifyOnce(key, deliver)).toBe("duplicate");

    expect(deliver).toHaveBeenCalledTimes(1);
  });

  it("chaves de turnos e eventos diferentes não se bloqueiam", () => {
    const deliver = vi.fn(() => true);

    notifyOnce(notificationKey("t1", "turn-1", "done"), deliver);
    notifyOnce(notificationKey("t1", "turn-2", "done"), deliver);
    notifyOnce(notificationKey("t1", "turn-1", "approval"), deliver);
    notifyOnce(notificationKey("t2", "turn-1", "done"), deliver);

    expect(deliver).toHaveBeenCalledTimes(4);
  });

  it("canal indisponível não é entrega nem consome a chave", () => {
    const key = notificationKey("t1", "turn-1", "done");

    expect(notifyOnce(key, () => false)).toBe("not_delivered");
    expect(wasDelivered(key)).toBe(false);
    expect(notifyOnce(key, () => true)).toBe("delivered");
  });

  it("falha ao entregar vira não entregue, sem lançar", () => {
    const key = notificationKey("t1", "turn-1", "done");

    expect(
      notifyOnce(key, () => {
        throw new Error("boom");
      }),
    ).toBe("not_delivered");
    expect(wasDelivered(key)).toBe(false);
  });

  it("a chave sobrevive a um novo módulo na mesma sessão", () => {
    const key = notificationKey("t1", "turn-1", "done");
    notifyOnce(key, () => true);

    expect(
      JSON.parse(sessionStorage.getItem("vectora:delivered-notifications")!),
    ).toContain(key);
    expect(wasDelivered(key)).toBe(true);
  });

  it("guarda só as chaves mais recentes", () => {
    for (let index = 0; index < 250; index += 1) {
      notifyOnce(notificationKey("t", `turn-${index}`, "done"), () => true);
    }

    expect(wasDelivered(notificationKey("t", "turn-0", "done"))).toBe(false);
    expect(wasDelivered(notificationKey("t", "turn-249", "done"))).toBe(true);
  });

  it("funciona sem sessionStorage, só em memória", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("bloqueado");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("bloqueado");
    });
    const key = notificationKey("t9", "turn-mem", "done");

    expect(notifyOnce(key, () => true)).toBe("delivered");
    expect(notifyOnce(key, () => true)).toBe("duplicate");
    vi.restoreAllMocks();
  });
});

describe("deliverLocalNotification", () => {
  it("sem Notification no ambiente, não entrega", () => {
    vi.stubGlobal("Notification", undefined);

    expect(deliverLocalNotification("Vectora", "pronto")).toBe(false);
  });

  it("sem permissão concedida, não entrega", () => {
    const ctor = vi.fn();
    vi.stubGlobal(
      "Notification",
      Object.assign(ctor, { permission: "denied" }),
    );

    expect(deliverLocalNotification("Vectora", "pronto")).toBe(false);
    expect(ctor).not.toHaveBeenCalled();
  });

  it("com permissão concedida, cria o alerta do sistema", () => {
    const ctor = vi.fn();
    vi.stubGlobal(
      "Notification",
      Object.assign(ctor, { permission: "granted" }),
    );

    expect(deliverLocalNotification("Vectora", "pronto")).toBe(true);
    expect(ctor).toHaveBeenCalledWith("Vectora", {
      body: "pronto",
      icon: "/vectora.svg",
    });
  });
});
