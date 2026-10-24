// whisper-batch — whisper.cpp counterpart of `presspeech-bench --batch`.
//
// Loads one ggml Whisper model once, runs the first listed clip as an
// unmeasured warmup, then times whisper_full() once per clip and writes one
// JSON object per clip plus a final summary object. Decoding parameters
// mirror whisper-cli's defaults (beam search 5, best-of 5, temperature
// fallback, flash attention, Metal GPU, 4 threads) so Whisper is measured in
// its standard Apple Silicon configuration rather than a handicapped one.
//
// Input clips must be 16 kHz mono 16-bit PCM WAV (run-engine-leaderboard.sh
// prepares every corpus in that format for all engines).
//
// Build: see run-engine-leaderboard.sh (clang against Homebrew whisper.cpp).

#include <ggml-backend.h>
#include <whisper.h>

#include <mach/mach.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static double now_seconds(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

static uint64_t footprint_bytes(void) {
    task_vm_info_data_t info;
    mach_msg_type_number_t count = TASK_VM_INFO_COUNT;
    if (task_info(mach_task_self(), TASK_VM_INFO, (task_info_t)&info, &count) != KERN_SUCCESS) {
        return 0;
    }
    return info.phys_footprint;
}

static uint32_t le32(const unsigned char *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint16_t le16(const unsigned char *p) {
    return (uint16_t)(p[0] | (p[1] << 8));
}

// Returns a malloc'd float buffer in [-1, 1], or NULL on any format mismatch.
static float *read_wav_pcm16_16k_mono(const char *path, int *n_samples) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (size < 12) { fclose(f); return NULL; }
    unsigned char *blob = malloc((size_t)size);
    if (!blob || fread(blob, 1, (size_t)size, f) != (size_t)size) { fclose(f); free(blob); return NULL; }
    fclose(f);
    float *out = NULL;
    if (memcmp(blob, "RIFF", 4) != 0 || memcmp(blob + 8, "WAVE", 4) != 0) goto done;
    bool fmt_ok = false;
    long offset = 12;
    while (offset + 8 <= size) {
        const unsigned char *chunk = blob + offset;
        uint32_t chunk_size = le32(chunk + 4);
        long start = offset + 8;
        if (start + (long)chunk_size > size) goto done;
        if (memcmp(chunk, "fmt ", 4) == 0 && chunk_size >= 16) {
            uint16_t format = le16(blob + start);
            uint16_t channels = le16(blob + start + 2);
            uint32_t rate = le32(blob + start + 4);
            uint16_t bits = le16(blob + start + 14);
            bool pcm = format == 1;
            if (format == 0xFFFE && chunk_size >= 40) pcm = le16(blob + start + 24) == 1;
            fmt_ok = pcm && channels == 1 && rate == 16000 && bits == 16;
        } else if (memcmp(chunk, "data", 4) == 0 && fmt_ok) {
            int n = (int)(chunk_size / 2);
            out = malloc(sizeof(float) * (size_t)(n > 0 ? n : 1));
            if (!out) goto done;
            for (int i = 0; i < n; i++) {
                int16_t v = (int16_t)le16(blob + start + 2 * i);
                out[i] = (float)v / 32768.0f;
            }
            *n_samples = n;
            goto done;
        }
        offset = start + chunk_size + (chunk_size & 1);
    }
done:
    free(blob);
    return out;
}

static void write_json_string(FILE *out, const char *s) {
    fputc('"', out);
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        switch (*p) {
            case '"': fputs("\\\"", out); break;
            case '\\': fputs("\\\\", out); break;
            case '\n': fputs("\\n", out); break;
            case '\r': fputs("\\r", out); break;
            case '\t': fputs("\\t", out); break;
            default:
                if (*p < 0x20) fprintf(out, "\\u%04x", *p);
                else fputc(*p, out);
        }
    }
    fputc('"', out);
}

static void quiet_log(enum ggml_log_level level, const char *text, void *user_data) {
    (void)user_data;
    if (level >= GGML_LOG_LEVEL_WARN) fputs(text, stderr);
}

static int usage(void) {
    fprintf(stderr,
            "usage: whisper-batch --model <ggml.bin> --batch <tsv> --batch-output <jsonl>\n"
            "                     [--language <en|de|...|auto>] [--threads N] [--beam-size N]\n"
            "                     [--best-of N] [--no-gpu] [--no-flash-attn]\n");
    return 2;
}

int main(int argc, char **argv) {
    const char *model = NULL, *list = NULL, *output = NULL, *language = "en";
    int threads = 4, beam_size = 5, best_of = 5;
    bool use_gpu = true, flash_attn = true;
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        const char *v = i + 1 < argc ? argv[i + 1] : NULL;
        if (!strcmp(a, "--model") && v) { model = v; i++; }
        else if (!strcmp(a, "--batch") && v) { list = v; i++; }
        else if (!strcmp(a, "--batch-output") && v) { output = v; i++; }
        else if (!strcmp(a, "--language") && v) { language = v; i++; }
        else if (!strcmp(a, "--threads") && v) { threads = atoi(v); i++; }
        else if (!strcmp(a, "--beam-size") && v) { beam_size = atoi(v); i++; }
        else if (!strcmp(a, "--best-of") && v) { best_of = atoi(v); i++; }
        else if (!strcmp(a, "--no-gpu")) { use_gpu = false; }
        else if (!strcmp(a, "--no-flash-attn")) { flash_attn = false; }
        else return usage();
    }
    if (!model || !list || !output || threads < 1 || beam_size < 1 || best_of < 1) return usage();

    // Read the clip list: <clip id>\t<wav path> per line.
    FILE *lf = fopen(list, "r");
    if (!lf) { fprintf(stderr, "cannot open batch list\n"); return 1; }
    size_t cap = 64, n_clips = 0;
    char **ids = malloc(cap * sizeof(char *)), **paths = malloc(cap * sizeof(char *));
    char line[8192];
    while (fgets(line, sizeof line, lf)) {
        size_t len = strcspn(line, "\r\n");
        line[len] = 0;
        if (!len) continue;
        char *tab = strchr(line, '\t');
        if (!tab || tab == line || !tab[1] || strchr(tab + 1, '\t')) {
            fprintf(stderr, "batch list row %zu is not <clip id><TAB><wav path>\n", n_clips + 1);
            return 1;
        }
        *tab = 0;
        if (n_clips == cap) {
            cap *= 2;
            ids = realloc(ids, cap * sizeof(char *));
            paths = realloc(paths, cap * sizeof(char *));
        }
        ids[n_clips] = strdup(line);
        paths[n_clips] = strdup(tab + 1);
        n_clips++;
    }
    fclose(lf);
    if (!n_clips) { fprintf(stderr, "batch list has no clips\n"); return 1; }

    whisper_log_set(quiet_log, NULL);
    // Homebrew ggml ships its CPU/Metal backends as loadable modules, as
    // whisper-cli does, load them before creating the context.
    ggml_backend_load_all();
    uint64_t baseline = footprint_bytes();
    double prep_t0 = now_seconds();
    struct whisper_context_params cparams = whisper_context_default_params();
    cparams.use_gpu = use_gpu;
    cparams.flash_attn = flash_attn;
    struct whisper_context *ctx = whisper_init_from_file_with_params(model, cparams);
    if (!ctx) { fprintf(stderr, "failed to load model\n"); return 1; }

    // whisper-cli defaults (examples/cli/cli.cpp in whisper.cpp 1.9.x).
    struct whisper_full_params wparams =
        whisper_full_default_params(beam_size > 1 ? WHISPER_SAMPLING_BEAM_SEARCH : WHISPER_SAMPLING_GREEDY);
    wparams.print_realtime = false;
    wparams.print_progress = false;
    wparams.print_timestamps = false;
    wparams.print_special = false;
    wparams.translate = false;
    wparams.language = language;
    wparams.detect_language = false;
    wparams.n_threads = threads;
    wparams.greedy.best_of = best_of;
    wparams.beam_search.beam_size = beam_size;
    wparams.temperature_inc = 0.2f;
    wparams.entropy_thold = 2.4f;
    wparams.logprob_thold = -1.0f;
    wparams.no_speech_thold = 0.6f;
    wparams.no_timestamps = false;

    int warm_n = 0;
    float *warm = read_wav_pcm16_16k_mono(paths[0], &warm_n);
    if (!warm || whisper_full(ctx, wparams, warm, warm_n) != 0) {
        fprintf(stderr, "warmup failed on clip 1\n");
        return 1;
    }
    free(warm);
    double prepare_seconds = now_seconds() - prep_t0;
    fprintf(stderr, "whisper-batch: %zu clips, model=%s, language=%s, threads=%d, beam=%d, best_of=%d, "
            "gpu=%d, flash_attn=%d; ready in %.1f ms\n", n_clips, model, language, threads, beam_size,
            best_of, use_gpu, flash_attn, prepare_seconds * 1000);

    FILE *out = fopen(output, "w");
    if (!out) { fprintf(stderr, "cannot create batch output\n"); return 1; }
    uint64_t peak = footprint_bytes();
    int failures = 0;
    size_t text_cap = 1 << 16;
    char *text = malloc(text_cap);
    for (size_t i = 0; i < n_clips; i++) {
        int n = 0;
        float *pcm = read_wav_pcm16_16k_mono(paths[i], &n);
        if (!pcm) { fprintf(stderr, "clip %zu is not 16 kHz mono PCM16 WAV\n", i + 1); return 1; }
        double t0 = now_seconds();
        int rc = whisper_full(ctx, wparams, pcm, n);
        double elapsed = now_seconds() - t0;
        free(pcm);
        text[0] = 0;
        size_t used = 0;
        const char *detected = "";
        if (rc == 0) {
            int segments = whisper_full_n_segments(ctx);
            for (int s = 0; s < segments; s++) {
                const char *seg = whisper_full_get_segment_text(ctx, s);
                size_t len = strlen(seg);
                while (used + len + 1 > text_cap) { text_cap *= 2; text = realloc(text, text_cap); }
                memcpy(text + used, seg, len + 1);
                used += len;
            }
            detected = whisper_lang_str(whisper_full_lang_id(ctx));
        } else {
            failures++;
        }
        uint64_t fp = footprint_bytes();
        if (fp > peak) peak = fp;
        fprintf(out, "{\"audio_seconds\":%.6f,", (double)n / 16000.0);
        if (rc == 0) fprintf(out, "\"detected_language\":\"%s\",\"elapsed_seconds\":%.6f,", detected ? detected : "", elapsed);
        else fprintf(out, "\"error\":\"whisper_full returned %d\",", rc);
        fputs("\"id\":", out);
        write_json_string(out, ids[i]);
        fprintf(out, ",\"index\":%zu,\"text\":", i);
        write_json_string(out, text);
        fputs(",\"type\":\"clip\"}\n", out);
        fflush(out);
        if ((i + 1) % 50 == 0 || i + 1 == n_clips) fprintf(stderr, "  %zu/%zu clips\n", i + 1, n_clips);
    }
    fprintf(out,
            "{\"backend\":\"whisper.cpp\",\"baseline_footprint_bytes\":%llu,\"beam_size\":%d,\"best_of\":%d,"
            "\"clips\":%zu,\"failures\":%d,\"flash_attn\":%s,\"language\":",
            (unsigned long long)baseline, beam_size, best_of, n_clips, failures, flash_attn ? "true" : "false");
    write_json_string(out, language);
    fprintf(out, ",\"peak_footprint_bytes\":%llu,\"prepare_seconds\":%.6f,\"system_info\":",
            (unsigned long long)peak, prepare_seconds);
    write_json_string(out, whisper_print_system_info());
    fprintf(out, ",\"threads\":%d,\"type\":\"summary\",\"use_gpu\":%s,\"whisper_version\":",
            threads, use_gpu ? "true" : "false");
    write_json_string(out, whisper_version());
    fputs("}\n", out);
    fclose(out);
    whisper_free(ctx);
    return 0;
}
