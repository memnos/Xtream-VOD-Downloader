using EmbyLibraryMerge.Services;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Logging;
using MediaBrowser.Model.Tasks;

namespace EmbyLibraryMerge.Tasks;

public class MergeSeriesTask : IScheduledTask
{
    private readonly DuplicateMergeService _mergeService;
    private readonly ILogger _logger;

    public MergeSeriesTask(ILibraryManager libraryManager, ILogger logger)
    {
        _logger = logger;
        _mergeService = new DuplicateMergeService(libraryManager, logger);
    }

    public string Name => "Unisci serie duplicate (MKV + STRM)";

    public string Key => "EmbyLibraryMergeSeries";

    public string Description =>
        "Unisce serie TV con lo stesso TMDB ID tra /data/tv, /data/tv-2 e /data/strm/series.";

    public string Category => "Library Merge";

    public IEnumerable<TaskTriggerInfo> GetDefaultTriggers()
    {
        yield break;
    }

    public Task Execute(CancellationToken cancellationToken, IProgress<double> progress)
    {
        _logger.Info("Library Merge: series task started");
        var result = _mergeService.MergeSeries(progress, cancellationToken);
        _logger.Info("Library Merge: merged {0} series groups", result.SeriesGroupsMerged);
        progress.Report(100);
        return Task.CompletedTask;
    }
}
