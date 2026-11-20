# EngiPad – Fluid properties with CoolProp

EngiPad can look up properties of fluids (density, heat capacity, viscosity, enthalpy, saturation data, …) and of humid air. The values come from the open-source library [CoolProp](http://www.coolprop.org), which is included in the EngiPad downloads.

Two functions do everything:

```text
prop(quantity, name, value, name, value, fluid)
haprop(quantity, name, value, name, value, name, value)        humid air
```

The order is the same as in CoolProp's `PropsSI` and `HAPropsSI` (and its Excel add-in): first the quantity you want, then the two (or three) known quantities as *name, value*, and for `prop` the fluid at the end. Names are written **without quotation marks**.

**Contents**

1. [Quick start](#1-quick-start)
2. [Quantities of `prop`](#2-quantities-of-prop)
3. [Saturation and wet steam](#3-saturation-and-wet-steam)
4. [Fluids](#4-fluids)
5. [Humid air: `haprop`](#5-humid-air-haprop)
6. [Units and checks](#6-units-and-checks)
7. [Properties in functions, plots, `nsolve` and `nintegrate`](#7-properties-in-functions-plots-nsolve-and-nintegrate)
8. [Error messages](#8-error-messages)
9. [Limits and technical notes](#9-limits-and-technical-notes)

---

## 1. Quick start

```text
ρ = prop(ρ, T, 20'degC, p, 1'bar, Water)
c_p = prop(c_p, T, 20'degC, p, 1'bar, Water) | kJ/(kg*K)
λ = prop(λ, T, 20'degC, p, 1'bar, Water)
Pr = prop(Pr, T, 300'K, p, 1'bar, Air)
```

Results: 998.21 kg/m³, 4.1841 kJ/(kg K), 0.59801 W/(m K), 0.70705.

The result is a normal quantity with its unit; you calculate with it and convert it with `| unit` as usual.

Your own variables can be used for the values:

```text
T_1 = 350'K ; p_1 = 2'bar
ρ_1 = prop(ρ, T, T_1, p, p_1, Air)
```

The words in the *name* positions are read as names, not as variables. If you have a variable called `T`, `prop(ρ, T, T, p, p, Air)` still works: the first `T` is the name "temperature", the second is your variable.

In the toolbar, the *Functions* menu has buttons that insert `prop(…)` and `haprop(…)`.

---

## 2. Quantities of `prop`

| Quantity | Name | Unit of the result | As input |
|---|---|---|---|
| Temperature | `T` | K | yes |
| Pressure | `p` | Pa | yes |
| Density | `ρ` or `rho` | kg/m³ | yes |
| Specific enthalpy | `h` | J/kg | yes |
| Specific entropy | `s` | J/(kg K) | yes |
| Specific internal energy | `u` | J/kg | yes |
| Vapour quality | `x` | – (0 … 1) | yes |
| Specific heat capacity, constant pressure | `c_p` or `cp` | J/(kg K) | |
| Specific heat capacity, constant volume | `c_v` or `cv` | J/(kg K) | |
| Thermal conductivity | `λ` or `lambda` | W/(m K) | |
| Dynamic viscosity | `η` or `eta` | Pa s | |
| Kinematic viscosity | `ν` or `nu` | m²/s | |
| Prandtl number | `Pr` | – | |
| Thermal diffusivity | `a` | m²/s | |
| Isobaric expansion coefficient | `β` or `beta` | 1/K | |
| Surface tension | `σ` or `sigma` | N/m | |
| Speed of sound | `c_s` | m/s | |

- Two **different** input quantities are needed. The usual pairs are `T` and `p`, `p` and `h`, `p` and `s`, `T` and `x`, `p` and `x`. Not every pair is supported by CoolProp for every fluid; in that case you get CoolProp's message.
- `ν` is calculated as η/ρ and `a` as λ/(ρ c_p).
- Results are in SI units. Append `| unit` for another one: `| kJ/kg`, `| degC`, `| bar`, `| mPa*s`, `| mm^2/s`.

---

## 3. Saturation and wet steam

Use the vapour quality `x` as the second input: `x = 0` is the boiling liquid, `x = 1` the saturated vapour.

```text
T_s = prop(T, p, 1'bar, x, 0, Water) | degC
p_s = prop(p, T, 100'degC, x, 0, Water) | bar
h_1 = prop(h, p, 1'bar, x, 0, Water) | kJ/kg
h_2 = prop(h, p, 1'bar, x, 1, Water) | kJ/kg
r = h_2 - h_1 | kJ/kg
```

Results: 99.606 °C, 1.0142 bar, 417.5 kJ/kg, 2674.9 kJ/kg; `r` = 2257.4 kJ/kg is the enthalpy of vaporisation.

Wet steam: `prop(h, p, 1'bar, x, 0.9, Water)`.

A state given by `T` and `p` exactly on the saturation line is ambiguous; give `x` instead of one of them.

---

## 4. Fluids

Use CoolProp's English fluid names, written as one word without special characters. Upper and lower case do not matter.

| Group | Names |
|---|---|
| Common | `Water`, `Air`, `Nitrogen`, `Oxygen`, `Hydrogen`, `Helium`, `Argon`, `CO2` (or `CarbonDioxide`), `Ammonia` (or `NH3`), `Methane`, `Ethane`, `Propane`, `nButane`, `IsoButane`, `Ethanol`, `Methanol` |
| Refrigerants | `R134a`, `R32`, `R125`, `R410A`, `R404A`, `R407C`, `R22`, `R1234yf`, `R1234zeE`, `R290` (propane), `R600a` (isobutane), `R717` (ammonia), `R744` (CO₂) |

EngiPad uses CoolProp 6.8.0, which knows 124 pure and pseudo-pure fluids. The complete list is on the [CoolProp website](http://www.coolprop.org/fluid_properties/PurePseudoPure.html#list-of-fluids). Names that contain a hyphen or brackets there have an alias without them (`n-Butane` → `nButane`, `R1234ze(E)` → `R1234zeE`).

`Air` is dry air treated as one substance. For air with water vapour use `haprop`.

---

## 5. Humid air: `haprop`

`haprop` needs **three** known quantities, normally temperature, pressure and one quantity for the moisture.

```text
h = haprop(h, T, 25'degC, p, 1.01325'bar, φ, 0.6) | kJ/kg
x = haprop(x, T, 25'degC, p, 1.01325'bar, φ, 0.6)
T_tau = haprop(T_tau, T, 25'degC, p, 1.01325'bar, φ, 0.6) | degC
φ_2 = haprop(φ, T, 30'degC, p, 1.01325'bar, x, x)
```

Results: 55.574 kJ/kg, 0.011949, 16.704 °C. The last line gives the relative humidity at 30 °C with the same water content: 0.44778.

| Quantity | Name | Unit of the result | As input |
|---|---|---|---|
| Temperature (dry bulb) | `T` | K | yes |
| Total pressure | `p` | Pa | yes |
| Relative humidity | `φ` or `phi` | – (0 … 1) | yes |
| Water content | `x` | kg water per kg dry air | yes |
| Dew point | `T_tau` | K | yes |
| Wet-bulb temperature | `T_f` | K | yes |
| Specific enthalpy | `h` | J per kg dry air | yes |
| Specific entropy | `s` | J/(kg K), per kg dry air | |
| Specific volume | `v` | m³ per kg dry air | |
| Specific heat capacity | `c_p` or `cp` | J/(kg K), per kg dry air | |
| Thermal conductivity | `λ` or `lambda` | W/(m K) | |
| Dynamic viscosity | `η` or `eta` | Pa s | |
| Density of the humid air | `ρ` or `rho` | kg/m³ | |
| Partial pressure of the water vapour | `p_D` | Pa | |

- The relative humidity is a number between 0 and 1: 60 % is `0.6`.
- `h`, `s`, `v` and `c_p` refer to one kilogram of **dry** air, as in the h,x diagram. The density `ρ` is that of the humid air, (1 + x)/v.
- In `prop`, `x` is the vapour quality; in `haprop`, `x` is the water content.

---

## 6. Units and checks

- Every value must have the kind of unit its name asks for: a temperature for `T` (K or `degC`), a pressure for `p` (Pa, bar, kPa, …), and so on. `prop(ρ, T, 20, p, 1'bar, Water)` is an error, because `20` has no unit.
- `x` and `φ` are plain numbers.
- Temperatures given in `degC` are converted correctly. Temperature results are in K; append `| degC`.
- Everything is passed to CoolProp in SI base units, so you may mix units freely: `prop(ρ, T, 300'K, p, 2000'hPa, Air)`.

---

## 7. Properties in functions, plots, `nsolve` and `nintegrate`

Wrap a property in a function of your own to get it as a curve:

```text
ρ_T(t) = prop(ρ, T, t, p, 1'bar, Water)
ρ_T(300'K)
plot(ρ_T(t), t, 280, 370)
```

In a plot the variable is a plain number in the SI base unit (here K), like every plot variable. Where CoolProp has no value (for example a saturation pressure above the critical temperature) the curve has a gap.

Solving for a state and integrating work the same way; the unit of the start value and of the limits says what the variable is:

```text
T_b = nsolve(prop(p, T, t, x, 0, Water) - 2'bar, t, 380'K) | degC
q = nintegrate(prop(c_p, T, t, p, 1'bar, Water), (t, 20'degC, 80'degC)) | kJ/kg
```

Results: 120.21 °C and 251.05 kJ/kg.

Often there is a direct way that is quicker and clearer: the boiling temperature is `prop(T, p, 2'bar, x, 0, Water)`, and the heat for warming up is the enthalpy difference `prop(h, T, 80'degC, …) - prop(h, T, 20'degC, …)`.

---

## 8. Error messages

| Message | Meaning |
|---|---|
| *'Wasser' is not a fluid CoolProp knows* | Use the English name, see [Fluids](#4-fluids). |
| *'κ' is not a known quantity* | The name is not in the tables above; the message lists the known names. |
| *the value of T must be a quantity with a matching unit* | The unit is missing or of the wrong kind. |
| *two DIFFERENT input quantities are needed* | The same name was given twice. |
| *argument 2 must be a name* | A value stands where a name is expected; check the order. |
| *CoolProp: …* | CoolProp could not calculate this state, e.g. a temperature outside the valid range of the fluid or an input pair it does not support. |
| *CoolProp gives no value for … at this state* | The quantity is not defined there, e.g. some transport properties inside the two-phase region. |
| *need the package CoolProp, which is not installed* | Only when EngiPad is run from source without the package: `pip install coolprop==6.8.0`. |

---

## 9. Limits and technical notes

- **Pure fluids only.** Mixtures with a composition, brines and secondary coolants (CoolProp's incompressible fluids) and the tabular backends are not available.
- **Trust, but check.** The values are those of CoolProp's reference equations of state. Near the critical point, in the two-phase region and at the edges of a fluid's range, properties can be undefined or less accurate. Compare an unusual value with a table once.
- **Version.** EngiPad is built with CoolProp **6.8.0**. Compared with the current CoolProp 8.0.0 the twelve newest fluids are missing (some recent refrigerants, chlorine, propylene glycol, tetrahydrofuran, vinyl chloride); 70 property values compared between the two versions agreed to at least eleven digits. The older version is used because it loads in about 0.2 s instead of almost 3 s, and it is loaded for every calculation that uses a property.
- **Speed.** A sheet that uses `prop` or `haprop` takes about 0.2 s longer per calculation. Sheets without them are not affected: CoolProp is only loaded when it is needed. A plot evaluates the property at 400 points and takes a little under a second.
- **Running from source.** `pip install -r requirements.txt` installs CoolProp. Without it EngiPad works normally and only `prop` and `haprop` report that the package is missing.
- **Licence.** CoolProp is distributed under the MIT licence. If you publish results, please cite CoolProp as described on its website.
