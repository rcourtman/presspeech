"""Model-free word alignment diagnostics for local speech benchmarks."""


def word_error_alignment(reference, hypothesis):
    """Return (minimum word edits, longest reference-deletion run).

    Ties choose diagonal, then deletion, then insertion, matching the macOS
    benchmark's stable alignment policy. Only two rows are retained so a
    long dictation does not need a quadratic backtracking matrix. A deletion
    run is path-dependent; it is a diagnostic, not proof of where audio was
    lost or how many words were spoken at a particular window seam.
    """
    width = len(hypothesis)
    previous_errors = list(range(width + 1))
    previous_runs = [0] * (width + 1)
    previous_maxima = [0] * (width + 1)

    for index, word in enumerate(reference, 1):
        errors = [index] + [0] * width
        runs = [index] + [0] * width
        maxima = [index] + [0] * width
        for column, candidate in enumerate(hypothesis, 1):
            # Strictly lower cost replaces the current path. Equal-cost
            # paths retain the earlier diagonal/deletion choice.
            best = previous_errors[column - 1] + (word != candidate)
            current_run = 0
            maximum = previous_maxima[column - 1]

            deletion = previous_errors[column] + 1
            if deletion < best:
                best = deletion
                current_run = previous_runs[column] + 1
                maximum = max(previous_maxima[column], current_run)

            insertion = errors[column - 1] + 1
            if insertion < best:
                best = insertion
                current_run = 0
                maximum = maxima[column - 1]

            errors[column] = best
            runs[column] = current_run
            maxima[column] = maximum

        previous_errors = errors
        previous_runs = runs
        previous_maxima = maxima

    return previous_errors[width], previous_maxima[width]
