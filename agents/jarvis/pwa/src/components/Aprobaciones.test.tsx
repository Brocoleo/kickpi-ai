import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Aprobaciones } from "./Aprobaciones";
import { ApiError } from "../api";
import type { Approval } from "../types";

const apiMock = vi.hoisted(() => ({
  approvals: vi.fn(),
  approval: vi.fn(),
  approve: vi.fn(),
  reject: vi.fn(),
  execute: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, api: apiMock };
});

const NOW = Date.now();

function makeApproval(overrides: Partial<Approval> = {}): Approval {
  return {
    id: "abc123",
    created_at: new Date(NOW - 60_000).toISOString(),
    requested_by: "agente",
    approved_by: null,
    host: "macbook",
    project_id: null,
    operation: "host_git_diff_stat",
    params: { path: "/home/leandro/kickpi-ai" },
    argv: ["git", "--no-optional-locks", "-C", "/home/leandro/kickpi-ai", "diff", "--stat"],
    resolved_path: "/home/leandro/kickpi-ai",
    timeout_s: 30,
    request_hash: "rh",
    decision_hash: "dh",
    risk: "read",
    risk_description: "Resumen de los cambios sin commitear.",
    expires_at: new Date(NOW + 240_000).toISOString(),
    status: "pending",
    decided_at: null,
    executed_at: null,
    exit_code: null,
    duration_ms: null,
    result_error: null,
    ...overrides,
  };
}

function renderTab(props: Partial<React.ComponentProps<typeof Aprobaciones>> = {}) {
  const onUnauthorized = vi.fn();
  const onPendingCount = vi.fn();
  const view = render(
    <Aprobaciones
      csrfToken="csrf-123"
      onUnauthorized={onUnauthorized}
      onPendingCount={onPendingCount}
      {...props}
    />,
  );
  return { ...view, onUnauthorized, onPendingCount };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.approvals.mockResolvedValue({ approvals: [makeApproval()], count: 1 });
  apiMock.approve.mockResolvedValue({ approval: makeApproval({ status: "approved" }) });
  apiMock.reject.mockResolvedValue({ approval: makeApproval({ status: "rejected" }) });
  apiMock.execute.mockResolvedValue({
    ok: true,
    state: "consumed",
    approval_id: "abc123",
    exit_code: 0,
    stdout: " 4 files changed, 85 insertions(+), 20 deletions(-)",
    stderr: null,
    duration_ms: 42,
    error: null,
  });
});

describe("listado", () => {
  it("muestra los datos de la solicitud y el contador", async () => {
    renderTab();
    await waitFor(() => expect(screen.getByText("host_git_diff_stat")).toBeInTheDocument());

    expect(screen.getByText("macbook")).toBeInTheDocument();
    expect(screen.getByText("Resumen de los cambios sin commitear.")).toBeInTheDocument();
    expect(
      screen.getByText("git --no-optional-locks -C /home/leandro/kickpi-ai diff --stat"),
    ).toBeInTheDocument();
    expect(screen.getByText("read")).toBeInTheDocument();
    // La cuenta atras es visible.
    expect(screen.getByRole("timer")).toBeInTheDocument();
  });

  it("informa del numero de pendientes al contenedor", async () => {
    const { onPendingCount } = renderTab();
    await waitFor(() => expect(onPendingCount).toHaveBeenCalledWith(1));
  });

  it("muestra un estado vacio cuando no hay nada", async () => {
    apiMock.approvals.mockResolvedValue({ approvals: [], count: 0 });
    renderTab();
    await waitFor(() =>
      expect(screen.getByText(/No hay solicitudes/)).toBeInTheDocument(),
    );
  });

  it("muestra un aviso si el servicio no responde", async () => {
    apiMock.approvals.mockRejectedValue(new ApiError(503, "sin servicio"));
    renderTab();
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("sin servicio"));
  });
});

describe("confirmación en dos pasos", () => {
  it("abrir el diálogo NO llama a la API", async () => {
    renderTab();
    await waitFor(() => expect(screen.getByText("host_git_diff_stat")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /^Aprobar y ejecutar/ }));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(apiMock.approve).not.toHaveBeenCalled();
    expect(apiMock.execute).not.toHaveBeenCalled();
  });

  it("el diálogo muestra los datos exactos del backend", async () => {
    renderTab();
    await waitFor(() => expect(screen.getByText("host_git_diff_stat")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /^Aprobar y ejecutar/ }));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("host_git_diff_stat");
    expect(dialog).toHaveTextContent("macbook");
    expect(dialog).toHaveTextContent(
      "git --no-optional-locks -C /home/leandro/kickpi-ai diff --stat",
    );
    expect(dialog).toHaveTextContent("/home/leandro/kickpi-ai");
    expect(dialog).toHaveTextContent("read");
  });

  it("confirmar aprueba y ejecuta, en ese orden y con el token CSRF", async () => {
    renderTab();
    await waitFor(() => expect(screen.getByText("host_git_diff_stat")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /^Aprobar y ejecutar/ }));
    fireEvent.click(screen.getByRole("button", { name: "Confirmar y ejecutar" }));

    await waitFor(() => expect(apiMock.execute).toHaveBeenCalled());
    expect(apiMock.approve).toHaveBeenCalledWith("abc123", "csrf-123");
    expect(apiMock.execute).toHaveBeenCalledWith("abc123", "csrf-123");
    expect(apiMock.approve.mock.invocationCallOrder[0]).toBeLessThan(
      apiMock.execute.mock.invocationCallOrder[0],
    );
    await waitFor(() =>
      expect(screen.getByText(/Aprobada y ejecutada: host_git_diff_stat/)).toBeInTheDocument(),
    );
    // El anuncio para lectores de pantalla es breve y no duplica el detalle.
    expect(screen.getByRole("status")).toHaveTextContent(
      "Solicitud aprobada y ejecutada correctamente.",
    );
  });

  it("cancelar no ejecuta nada", async () => {
    renderTab();
    await waitFor(() => expect(screen.getByText("host_git_diff_stat")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /^Aprobar y ejecutar/ }));
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(apiMock.approve).not.toHaveBeenCalled();
    expect(apiMock.execute).not.toHaveBeenCalled();
  });

  it("Escape cierra el diálogo", async () => {
    renderTab();
    await waitFor(() => expect(screen.getByText("host_git_diff_stat")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /^Aprobar y ejecutar/ }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("rechazar llama al endpoint de rechazo y no ejecuta", async () => {
    renderTab();
    await waitFor(() => expect(screen.getByText("host_git_diff_stat")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /^Rechazar/ }));
    fireEvent.click(screen.getByRole("button", { name: "Confirmar rechazo" }));

    await waitFor(() => expect(apiMock.reject).toHaveBeenCalledWith("abc123", "csrf-123", ""));
    expect(apiMock.execute).not.toHaveBeenCalled();
  });
});

describe("pulsaciones repetidas", () => {
  it("no se puede aprobar dos veces mientras la primera está en curso", async () => {
    let resolveApprove: (value: unknown) => void = () => {};
    apiMock.approve.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveApprove = resolve;
        }),
    );

    renderTab();
    await waitFor(() => expect(screen.getByText("host_git_diff_stat")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /^Aprobar y ejecutar/ }));
    const confirm = screen.getByRole("button", { name: "Confirmar y ejecutar" });
    fireEvent.click(confirm);

    // Mientras dura la peticion, el boton esta deshabilitado.
    await waitFor(() => expect(confirm).toBeDisabled());
    fireEvent.click(confirm);
    fireEvent.click(confirm);

    expect(apiMock.approve).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveApprove({ approval: makeApproval({ status: "approved" }) });
    });
    await waitFor(() => expect(apiMock.execute).toHaveBeenCalledTimes(1));
  });
});

describe("caducidad", () => {
  it("deshabilita aprobar si quedan menos de 10 segundos", async () => {
    apiMock.approvals.mockResolvedValue({
      approvals: [makeApproval({ expires_at: new Date(NOW + 5_000).toISOString() })],
      count: 1,
    });
    renderTab();
    await waitFor(() => expect(screen.getByText("host_git_diff_stat")).toBeInTheDocument());

    expect(screen.getByRole("button", { name: /No se puede aprobar/ })).toBeDisabled();
  });
});

describe("estados finales", () => {
  it("una solicitud fallida no se puede reintentar", async () => {
    apiMock.approvals.mockResolvedValue({
      approvals: [
        makeApproval({
          status: "failed",
          result_error: "ejecucion interrumpida; resultado incierto",
        }),
      ],
      count: 1,
    });
    renderTab();
    await waitFor(() => expect(screen.getByText("host_git_diff_stat")).toBeInTheDocument());

    expect(screen.queryByRole("button", { name: /^Aprobar y ejecutar/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Rechazar/ })).toBeNull();
    expect(screen.getByText(/Resultado incierto/)).toBeInTheDocument();
    expect(screen.getByText("fallida")).toBeInTheDocument();
  });

  it("una solicitud consumida no vuelve a ejecutarse", async () => {
    apiMock.approvals.mockResolvedValue({
      approvals: [makeApproval({ status: "consumed", exit_code: 0, duration_ms: 42 })],
      count: 1,
    });
    renderTab();
    await waitFor(() => expect(screen.getByText("host_git_diff_stat")).toBeInTheDocument());

    expect(screen.queryByRole("button", { name: /^Aprobar y ejecutar/ })).toBeNull();
    expect(screen.getByText(/Ya ejecutada/)).toBeInTheDocument();
    expect(screen.getByText("ejecutada")).toBeInTheDocument();
  });

  it("distingue expirada y rechazada", async () => {
    apiMock.approvals.mockResolvedValue({
      approvals: [
        makeApproval({ id: "e1", status: "expired" }),
        makeApproval({ id: "r1", status: "rejected" }),
      ],
      count: 2,
    });
    renderTab();
    await waitFor(() => expect(screen.getByText("expirada")).toBeInTheDocument());
    expect(screen.getByText("rechazada")).toBeInTheDocument();
  });
});

describe("errores", () => {
  it("un 409 se informa sin reintentar", async () => {
    apiMock.approve.mockRejectedValue(
      new ApiError(409, "la aprobacion ya fue consumida (un solo uso)"),
    );
    renderTab();
    await waitFor(() => expect(screen.getByText("host_git_diff_stat")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /^Aprobar y ejecutar/ }));
    fireEvent.click(screen.getByRole("button", { name: "Confirmar y ejecutar" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("ya fue consumida"),
    );
    expect(apiMock.execute).not.toHaveBeenCalled();
  });

  it("un 401 cierra la sesion", async () => {
    apiMock.approvals.mockRejectedValue(new ApiError(401, "No autenticado"));
    const { onUnauthorized } = renderTab();
    await waitFor(() => expect(onUnauthorized).toHaveBeenCalled());
  });

  it("un 403 se explica sin cerrar la sesion", async () => {
    apiMock.approve.mockRejectedValue(new ApiError(403, "Token CSRF inválido"));
    const { onUnauthorized } = renderTab();
    await waitFor(() => expect(screen.getByText("host_git_diff_stat")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /^Aprobar y ejecutar/ }));
    fireEvent.click(screen.getByRole("button", { name: "Confirmar y ejecutar" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("403"));
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("un fallo de red se informa como problema de conexion", async () => {
    apiMock.approve.mockRejectedValue(new TypeError("Failed to fetch"));
    apiMock.approval.mockRejectedValue(new TypeError("Failed to fetch"));
    renderTab();
    await waitFor(() => expect(screen.getByText("host_git_diff_stat")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /^Aprobar y ejecutar/ }));
    fireEvent.click(screen.getByRole("button", { name: "Confirmar y ejecutar" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/conexion|contactar/),
    );
  });
});

describe("resultado incierto (respuesta perdida)", () => {
  it("consulta el estado persistido y NO reintenta la ejecución", async () => {
    // El servidor ejecutó, pero la respuesta se perdió.
    apiMock.execute.mockRejectedValue(new TypeError("Failed to fetch"));
    apiMock.approval.mockResolvedValue({
      approval: makeApproval({ status: "consumed", exit_code: 0, duration_ms: 42 }),
    });

    renderTab();
    await waitFor(() => expect(screen.getByText("host_git_diff_stat")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /^Aprobar y ejecutar/ }));
    fireEvent.click(screen.getByRole("button", { name: "Confirmar y ejecutar" }));

    // Se consulta el estado real (una lectura), y no se vuelve a ejecutar.
    await waitFor(() => expect(apiMock.approval).toHaveBeenCalledWith("abc123"));
    expect(apiMock.execute).toHaveBeenCalledTimes(1);
    // Y la tarjeta refleja el estado que dice el servidor.
    await waitFor(() => expect(screen.getByText("ejecutada")).toBeInTheDocument());
  });

  it("si tampoco se puede consultar, avisa y bloquea la acción", async () => {
    apiMock.execute.mockRejectedValue(new TypeError("Failed to fetch"));
    apiMock.approval.mockRejectedValue(new TypeError("Failed to fetch"));

    renderTab();
    await waitFor(() => expect(screen.getByText("host_git_diff_stat")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /^Aprobar y ejecutar/ }));
    fireEvent.click(screen.getByRole("button", { name: "Confirmar y ejecutar" }));

    await waitFor(() =>
      expect(screen.getByText(/No se pudo confirmar el resultado/)).toBeInTheDocument(),
    );
    // No se ofrece volver a aprobar ni ejecutar: el estado es incierto.
    expect(screen.queryByRole("button", { name: /^Aprobar y ejecutar/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Rechazar/ })).toBeNull();
    // Pero sí se puede consultar el estado a mano.
    expect(
      screen.getByRole("button", { name: "Consultar estado" }),
    ).toBeInTheDocument();
  });

  it("un error de red NO se presenta como una ejecución fallida", async () => {
    apiMock.execute.mockRejectedValue(new TypeError("Failed to fetch"));
    apiMock.approval.mockRejectedValue(new TypeError("Failed to fetch"));

    renderTab();
    await waitFor(() => expect(screen.getByText("host_git_diff_stat")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /^Aprobar y ejecutar/ }));
    fireEvent.click(screen.getByRole("button", { name: "Confirmar y ejecutar" }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    const text = screen.getByRole("alert").textContent ?? "";
    expect(text).toMatch(/no se ha podido confirmar|no se pudo contactar/i);
    expect(text).not.toMatch(/falló la ejecución|ejecución fallida/i);
  });

  it("consultar estado a mano actualiza la tarjeta", async () => {
    apiMock.execute.mockRejectedValue(new TypeError("Failed to fetch"));
    apiMock.approval.mockRejectedValue(new TypeError("Failed to fetch"));

    renderTab();
    await waitFor(() => expect(screen.getByText("host_git_diff_stat")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /^Aprobar y ejecutar/ }));
    fireEvent.click(screen.getByRole("button", { name: "Confirmar y ejecutar" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Consultar estado" })).toBeInTheDocument(),
    );

    // Ahora el servidor responde: el estado real es "ejecutando".
    apiMock.approval.mockResolvedValue({
      approval: makeApproval({ status: "executing" }),
    });
    fireEvent.click(screen.getByRole("button", { name: "Consultar estado" }));

    await waitFor(() => expect(screen.getByText("ejecutando")).toBeInTheDocument());
    expect(apiMock.execute).toHaveBeenCalledTimes(1);
  });
});

describe("refresco automático", () => {
  it("actualiza cada 10 segundos", async () => {
    vi.useFakeTimers();
    try {
      renderTab();
      await vi.advanceTimersByTimeAsync(0);
      expect(apiMock.approvals).toHaveBeenCalledTimes(1);

      await vi.advanceTimersByTimeAsync(10_000);
      expect(apiMock.approvals).toHaveBeenCalledTimes(2);

      await vi.advanceTimersByTimeAsync(10_000);
      expect(apiMock.approvals).toHaveBeenCalledTimes(3);
    } finally {
      vi.useRealTimers();
    }
  });
});
