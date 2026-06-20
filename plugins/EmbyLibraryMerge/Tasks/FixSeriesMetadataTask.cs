using EmbyLibraryMerge.Services;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Logging;
using MediaBrowser.Model.Tasks;

namespace EmbyLibraryMerge.Tasks;

public class FixSeriesMetadataTask : IScheduledTask
{
    private readonly DuplicateMergeService _mergeService;
    private readonly ILogger _logger;

    public FixSeriesMetadataTask(ILibraryManager libraryManager, ILogger logger)
    {
        _logger = logger;
        _mergeService = new DuplicateMergeService(libraryManager, logger);
    }

    public string Name => "Ripara metadati serie (stagioni + episodi)";

    public string Key => "EmbyLibraryMergeFixSeries";

    public string Description =>
        "Imposta i numeri stagione mancanti e unisce episodi duplicati (stesso SxxExx, es. MKV + STRM).";

    public string Category => "Library Merge";

    public IEnumerable<TaskTriggerInfo> GetDefaultTriggers()
    {
        yield break;
    }

    public Task Execute(CancellationToken cancellationToken, IProgress<double> progress)
    {
        _logger.Info("Library Merge: fix series metadata started");
        var seasons = _mergeService.FixSeasonNumbers(progress, cancellationToken);
        var episodes = _mergeService.MergeDuplicateEpisodes(progress, cancellationToken);
        _logger.Info(
            "Library Merge: fixed {0} seasons, merged {1} duplicate episode groups",
            seasons.SeasonsFixed,
            episodes.EpisodeGroupsMerged);
        progress.Report(100);
        return Task.CompletedTask;
    }
}
