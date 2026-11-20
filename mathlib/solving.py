import sympy as sp


def _not_real(value) -> bool:
    """True only if SymPy KNOWS the value is not real (e.g. 2*I).

    A solution that still contains a variable without a value (F/m,
    sqrt(a), pi/omega_d) has is_real None -- SymPy cannot decide it
    without assumptions about the variables. Such solutions are kept:
    dropping them made solve(Eq(F, m*a), a) return an empty list.
    """
    return getattr(value, "is_real", None) is False


def my_solve(*args, **kwargs):
    """solve(): real solutions only; csolve() keeps the complex ones."""
    result = sp.solve(*args, **kwargs)

    if isinstance(result, list):
        filtered = []

        for sol in result:
            if isinstance(sol, dict):
                values = sol.values()
            elif isinstance(sol, (list, tuple)):
                values = sol
            else:
                values = (sol,)

            if not any(_not_real(v) for v in values):
                filtered.append(sol)

        result = filtered

        if len(result) == 1 and not isinstance(result[0], dict):
            return result[0]

    return result


def my_csolve(*args, **kwargs):
    result = sp.solve(*args, **kwargs)

    if isinstance(result, list) and len(result) == 1 and not isinstance(result[0], dict):
        return result[0]

    return result
