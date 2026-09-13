#!/usr/bin/env python3
"""
3D-Visualisierung und Interpolations-Prüfung für die Kennlinien
der Hocheffizienz-Umwälzpumpe Wilo-Varios PICO-STG 25/1-8.

Liest docs/wilo_varios_pico_stg_kennlinien.csv ein und erzeugt 3D-Plots:
1. Förderhöhe H = f(Q, PWM)
2. Leistung P1 = f(Q, PWM)
3. Firmware-Inversion: Durchfluss Q = f(PWM, P1) zur Validierung der Schätzung
"""

import os
import sys
import csv
import numpy as np
import matplotlib.pyplot as plt
from matplotlib import cm
from scipy.interpolate import griddata

def load_data(csv_path):
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Datei nicht gefunden: {csv_path}")

    pwm_steps = [5, 15, 25, 35, 45, 55, 65, 75, 85]
    
    qh_points = []  # (Q, PWM, H)
    qp_points = []  # (Q, PWM, P)
    inv_points = [] # (PWM, P, Q)

    with open(csv_path, mode='r', encoding='utf-8') as f:
        reader = csv.reader(f)
        header = []
        for row in reader:
            if not row or row[0].startswith('#'):
                continue
            if not header:
                header = [c.strip() for c in row]
                col_idx = {col: i for i, col in enumerate(header)}
                continue

            # Q-H Daten
            for pwm in pwm_steps:
                q_col = f"{pwm}_Q_m3h"
                h_col = f"{pwm}_H_m"
                if q_col in col_idx and h_col in col_idx:
                    q_val = row[col_idx[q_col]].strip()
                    h_val = row[col_idx[h_col]].strip()
                    if q_val and h_val:
                        try:
                            q = max(0.0, float(q_val))
                            h = max(0.0, float(h_val))
                            qh_points.append((q, pwm, h))
                        except ValueError:
                            pass

            # Q-P Daten
            for pwm in pwm_steps:
                q_col = f"{pwm}p_Q_m3h"
                p_col = f"{pwm}p_P_W"
                if q_col in col_idx and p_col in col_idx:
                    q_val = row[col_idx[q_col]].strip()
                    p_val = row[col_idx[p_col]].strip()
                    if q_val and p_val:
                        try:
                            q = max(0.0, float(q_val))
                            p = float(p_val)
                            if p <= 100.0: # Ausreißer filtern
                                qp_points.append((q, pwm, p))
                                inv_points.append((pwm, p, q))
                        except ValueError:
                            pass

    return (np.array(qh_points), np.array(qp_points), np.array(inv_points))

def plot_surface_and_points(ax, points, x_label, y_label, z_label, title, grid_res=60, cmap=cm.viridis):
    x = points[:, 0]
    y = points[:, 1]
    z = points[:, 2]

    # Grid für Interpolation aufspannen
    xi = np.linspace(x.min(), x.max(), grid_res)
    yi = np.linspace(y.min(), y.max(), grid_res)
    X, Y = np.meshgrid(xi, yi)

    # Lineare 2D-Gitterinterpolation (entspricht bilinearer Interpolation auf dem ESP32)
    Z = griddata((x, y), z, (X, Y), method='linear')

    # 3D-Oberfläche plotten
    surf = ax.plot_surface(X, Y, Z, cmap=cmap, alpha=0.75, edgecolor='none', antialiased=True)
    
    # Original-Messpunkte aus der CSV hervorheben
    ax.scatter(x, y, z, color='red', s=20, edgecolor='black', depthshade=True, label='CSV-Messpunkte')

    ax.set_xlabel(x_label, fontsize=10, labelpad=8)
    ax.set_ylabel(y_label, fontsize=10, labelpad=8)
    ax.set_zlabel(z_label, fontsize=10, labelpad=8)
    ax.set_title(title, fontsize=12, fontweight='bold', pad=12)
    ax.grid(True, linestyle='--', alpha=0.4)
    return surf

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(script_dir, ".."))
    csv_file = os.path.join(project_root, "docs", "wilo_varios_pico_stg_kennlinien.csv")
    output_png = os.path.join(project_root, "docs", "wilo_pump_curves_3d.png")

    print(f"[INFO] Lade Kennliniendaten aus: {csv_file}")
    qh_data, qp_data, inv_data = load_data(csv_file)
    print(f"[INFO] Geladene Messpunkte: Q-H: {len(qh_data)}, Q-P: {len(qp_data)}")

    fig = plt.figure(figsize=(18, 6))

    # 1. Plot: Förderhöhe H = f(Q, PWM)
    ax1 = fig.add_subplot(1, 3, 1, projection='3d')
    surf1 = plot_surface_and_points(
        ax1, qh_data,
        x_label="Volumenstrom Q [m³/h]",
        y_label="iPWM GT [%]",
        z_label="Förderhöhe H [m]",
        title="1. Förderhöhe H = f(Q, PWM)",
        cmap=cm.Blues_r
    )
    fig.colorbar(surf1, ax=ax1, shrink=0.5, aspect=12, pad=0.1)

    # 2. Plot: Leistung P1 = f(Q, PWM)
    ax2 = fig.add_subplot(1, 3, 2, projection='3d')
    surf2 = plot_surface_and_points(
        ax2, qp_data,
        x_label="Volumenstrom Q [m³/h]",
        y_label="iPWM GT [%]",
        z_label="Leistung P1 [W]",
        title="2. Leistung P1 = f(Q, PWM)",
        cmap=cm.plasma
    )
    fig.colorbar(surf2, ax=ax2, shrink=0.5, aspect=12, pad=0.1)

    # 3. Plot: Durchflussschätzung Q = f(PWM, P1)
    ax3 = fig.add_subplot(1, 3, 3, projection='3d')
    surf3 = plot_surface_and_points(
        ax3, inv_data,
        x_label="iPWM GT [%]",
        y_label="Leistung P1 [W]",
        z_label="Geschätztes Q [m³/h]",
        title="3. Inversion: Q = f(PWM, P1) [ESP32]",
        cmap=cm.viridis
    )
    fig.colorbar(surf3, ax=ax3, shrink=0.5, aspect=12, pad=0.1)

    plt.suptitle("Wilo-Varios PICO-STG 25/1-8 – 3D Kennlinienfeld & Interpolations-Validierung", 
                 fontsize=15, fontweight='bold', y=0.98)
    plt.tight_layout()

    print(f"[INFO] Speichere hochauflösenden 3D-Plot nach: {output_png}")
    plt.savefig(output_png, dpi=200, bbox_inches='tight')
    print("[SUCCESS] 3D-Plot erfolgreich als Bild gespeichert!")

    # Standardmäßig immer interaktives 3D-Fenster öffnen (außer --headless / --save-only ist gesetzt)
    if "--headless" in sys.argv or "--save-only" in sys.argv:
        print("[INFO] Headless-Modus: Interaktives Fenster übersprungen.")
    else:
        print("[INFO] Öffne interaktives 3D-Plot-Fenster (dreh- und zoombar per Maus, Fenster schließen zum Beenden)...")
        plt.show()

if __name__ == "__main__":
    main()
