using System.Text.RegularExpressions;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Entities.Movies;
using MediaBrowser.Controller.Entities.TV;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Logging;
using MediaBrowser.Model.Querying;

namespace EmbyLibraryMerge.Services;

public class DuplicateMergeService
{
    private static readonly Regex SeasonInPath = new(
        @"(?:Season|Stagione)\s*0*(\d+)",
        RegexOptions.IgnoreCase | RegexOptions.Compiled);

    private readonly ILibraryManager _libraryManager;
    private readonly ILogger _logger;

    public DuplicateMergeService(ILibraryManager libraryManager, ILogger logger)
    {
        _libraryManager = libraryManager;
        _logger = logger;
    }

    public MergeResult MergeMovies(IProgress<double>? progress, CancellationToken cancellationToken)
    {
        var config = Plugin.Instance.Configuration;
        var movies = _libraryManager
            .GetItemList(new InternalItemsQuery { Recursive = true, IsVirtualItem = false })
            .OfType<Movie>()
            .Where(m => !string.IsNullOrWhiteSpace(GetTmdb(m)))
            .Where(m => !IsExcluded(m.Path, config))
            .ToList();

        var groups = movies.GroupBy(GetTmdb!).Where(g => g.Count() > 1).ToList();
        var merged = 0;

        for (var i = 0; i < groups.Count; i++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            progress?.Report(groups.Count == 0 ? 100 : (i + 1) / (double)groups.Count * 100);

            var ordered = groups[i]
                .OrderByDescending(m => MoviePathScore(m.Path, config.PreferLocalMoviePaths))
                .Cast<BaseItem>()
                .ToArray();

            if (MergeGroup(ordered, "movie", groups[i].Key!))
            {
                merged++;
            }
        }

        return new MergeResult { MovieGroupsMerged = merged };
    }

    public MergeResult MergeSeries(IProgress<double>? progress, CancellationToken cancellationToken)
    {
        var config = Plugin.Instance.Configuration;
        var series = _libraryManager
            .GetItemList(new InternalItemsQuery { Recursive = true, IsVirtualItem = false })
            .OfType<Series>()
            .Where(s => !string.IsNullOrWhiteSpace(GetTmdb(s)))
            .Where(s => !IsExcluded(s.Path, config))
            .ToList();

        var groups = series.GroupBy(GetTmdb!).Where(g => g.Count() > 1).ToList();
        var merged = 0;

        for (var i = 0; i < groups.Count; i++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            progress?.Report(groups.Count == 0 ? 100 : (i + 1) / (double)groups.Count * 100);

            // Primary order for MergeItems only — all sources (strm + local) are kept as versions.
            var ordered = groups[i]
                .OrderByDescending(s => SeriesPathScore(s.Path, config.PreferLocalSeriesPaths))
                .ThenByDescending(s => EpisodeCount(s))
                .Cast<BaseItem>()
                .ToArray();

            if (MergeGroup(ordered, "series", groups[i].Key!))
            {
                merged++;
            }
        }

        return new MergeResult { SeriesGroupsMerged = merged };
    }

    public MergeResult FixSeasonNumbers(IProgress<double>? progress, CancellationToken cancellationToken)
    {
        var seasons = _libraryManager
            .GetItemList(new InternalItemsQuery { Recursive = true, IsVirtualItem = false })
            .OfType<Season>()
            .Where(s => !s.IndexNumber.HasValue)
            .ToList();

        var fixedCount = 0;
        for (var i = 0; i < seasons.Count; i++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            progress?.Report(seasons.Count == 0 ? 100 : (i + 1) / (double)seasons.Count * 100);

            var season = seasons[i];
            var number = ParseSeasonNumber(season.Path) ?? ParseSeasonNumber(season.Name);
            if (!number.HasValue)
            {
                continue;
            }

            season.IndexNumber = number;
            _libraryManager.UpdateItem(season, season.GetParent(), ItemUpdateType.MetadataEdit);
            fixedCount++;
        }

        _logger.Info("Library Merge: fixed {0} season numbers", fixedCount);
        return new MergeResult { SeasonsFixed = fixedCount };
    }

    public MergeResult MergeDuplicateEpisodes(IProgress<double>? progress, CancellationToken cancellationToken)
    {
        var episodes = _libraryManager
            .GetItemList(new InternalItemsQuery { Recursive = true, IsVirtualItem = false })
            .OfType<Episode>()
            .Where(e => e.ParentIndexNumber.HasValue && e.IndexNumber.HasValue)
            .ToList();

        var groups = episodes
            .GroupBy(e => $"{e.SeriesId}|{e.ParentIndexNumber}|{e.IndexNumber}")
            .Where(g => g.Count() > 1)
            .ToList();

        var merged = 0;
        for (var i = 0; i < groups.Count; i++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            progress?.Report(groups.Count == 0 ? 100 : (i + 1) / (double)groups.Count * 100);

            var ordered = groups[i]
                .OrderByDescending(e => EpisodePathScore(e.Path))
                .Cast<BaseItem>()
                .ToArray();

            if (MergeGroup(ordered, "episode", groups[i].Key))
            {
                merged++;
            }
        }

        return new MergeResult { EpisodeGroupsMerged = merged };
    }

    private bool MergeGroup(BaseItem[] ordered, string kind, string key)
    {
        try
        {
            _libraryManager.MergeItems(ordered);
            _logger.Info("Library Merge: merged {0} {1} key={2} -> {3}", kind, ordered.Length, key, ordered[0].Name);
            return true;
        }
        catch (Exception ex)
        {
            _logger.Error("Library Merge: {0} merge failed key={1}: {2}", kind, key, ex.Message);
            return false;
        }
    }

    private int EpisodeCount(Series series)
    {
        return _libraryManager
            .GetItemList(new InternalItemsQuery { Recursive = true, IsVirtualItem = false })
            .OfType<Episode>()
            .Count(e => e.SeriesId == series.InternalId);
    }

    private static int? ParseSeasonNumber(string? text)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return null;
        }

        var match = SeasonInPath.Match(text);
        return match.Success ? int.Parse(match.Groups[1].Value) : null;
    }

    private static string? GetTmdb(BaseItem item)
    {
        if (item.ProviderIds != null &&
            item.ProviderIds.TryGetValue("Tmdb", out var tmdb) &&
            !string.IsNullOrWhiteSpace(tmdb))
        {
            return tmdb;
        }

        return null;
    }

    private static bool IsExcluded(string? path, PluginConfiguration config)
    {
        if (string.IsNullOrWhiteSpace(path) || string.IsNullOrWhiteSpace(config.ExcludedPathContains))
        {
            return false;
        }

        var parts = config.ExcludedPathContains.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        return parts.Any(p => path.Contains(p, StringComparison.OrdinalIgnoreCase));
    }

    private static int MoviePathScore(string? path, bool preferLocal)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            return 0;
        }

        if (!preferLocal)
        {
            return 1;
        }

        var lower = path.ToLowerInvariant();
        if (lower.StartsWith("/data/movies/", StringComparison.Ordinal))
        {
            return 200;
        }

        if (lower.Contains("/strm/", StringComparison.Ordinal))
        {
            return 100;
        }

        return 50;
    }

    private static int SeriesPathScore(string? path, bool preferLocal)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            return 0;
        }

        if (!preferLocal)
        {
            return 1;
        }

        var lower = path.ToLowerInvariant();
        if (lower.StartsWith("/data/tv/", StringComparison.Ordinal) && !lower.StartsWith("/data/tv-2/", StringComparison.Ordinal))
        {
            return 300;
        }

        if (lower.StartsWith("/data/tv-2/", StringComparison.Ordinal))
        {
            return 200;
        }

        if (lower.Contains("/strm/", StringComparison.Ordinal))
        {
            return 100;
        }

        return 50;
    }

    private static int EpisodePathScore(string? path)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            return 0;
        }

        var lower = path.ToLowerInvariant();
        if (lower.EndsWith(".mkv", StringComparison.Ordinal) || lower.EndsWith(".mp4", StringComparison.Ordinal))
        {
            return 200;
        }

        if (lower.EndsWith(".strm", StringComparison.Ordinal))
        {
            return 100;
        }

        return 50;
    }
}

public sealed class MergeResult
{
    public int MovieGroupsMerged { get; set; }
    public int SeriesGroupsMerged { get; set; }
    public int SeasonsFixed { get; set; }
    public int EpisodeGroupsMerged { get; set; }
}
