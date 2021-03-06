"""
File: Analyzer.py
License: Part of the PIRA project. Licensed under BSD 3 clause license. See LICENSE.txt file at https://github.com/jplehr/pira/LICENSE.txt
Description: Module to encapsulate the underlying analysis engine.
"""

import lib.Utility as util
import lib.Logging as logging
import lib.TimeTracking as tt
import lib.FunctorManagement as fmg
import lib.DefaultFlags as defaults


class Analyzer:

  def __init__(self, configuration) -> None:
    self.config = configuration
    self.error = None
    self._profile_sink = None

  def set_profile_sink(self, sink) -> None:
    self._profile_sink = sink

  def analyze_local(self, flavor, build, benchmark, kwargs, iterationNumber) -> str:
    fm = fmg.FunctorManager()
    analyze_functor = fm.get_or_load_functor(build, benchmark, flavor, 'analyze')
    analyzer_dir = self.config.get_analyser_dir(build, benchmark)
    kwargs['analyzer_dir'] = analyzer_dir

    # The invoke args can be retrieved from the configuration object.
    # Since the invoke args are iterable, we can create all necessary argument tuples here.
    if self._profile_sink is None:
      raise RuntimeError('[Analyzer] Profile Sink in Analyzer not set!')

    # We construct a json file that contains the necesary information to be parsed vy the
    # PGIS tool. That way, we can make it easily traceable and debug from manual inspection.
    # This will be the new standard way of pusing information to PGIS.
    pgis_cfg_file = self._profile_sink.output_pgis_config(benchmark, analyzer_dir)

    if analyze_functor.get_method()['active']:
      analyze_functor.active(benchmark, **kwargs)

    else:
      logging.get_logger().log('Analyzer::analyze_local: Using passive mode')
      try:
        exp_dir = self.config.get_analyser_exp_dir(build, benchmark)
        isdirectory_good = util.check_provided_directory(analyzer_dir)
        command = analyze_functor.passive(benchmark, **kwargs)

        logging.get_logger().log('Analyzer::analyze_local: Command = ' + command)

        benchmark_name = self.config.get_benchmark_name(benchmark)

        if isdirectory_good:
          util.change_cwd(analyzer_dir)
          logging.get_logger().log('Analyzer::analyzer_local: Flavor = ' + flavor + ' | benchmark_name = ' +
                                   benchmark_name)
          instr_files = util.build_instr_file_path(analyzer_dir, flavor, benchmark_name)
          logging.get_logger().log('Analyzer::analyzer_local: instrumentation file = ' + instr_files)
          prev_instr_file = util.build_previous_instr_file_path(analyzer_dir, flavor, benchmark_name)

        tracker = tt.TimeTracker()
        
        # TODO: Alternate between expansion and pure filtering.
        if iterationNumber > 0 and util.is_file(instr_files):
          logging.get_logger().log('Analyzer::analyze_local: instr_file available')
          util.rename(instr_files, prev_instr_file)
          tracker.m_track('Analysis', util, 'run_analyser_command', command, analyzer_dir, flavor, benchmark_name,
                          exp_dir, iterationNumber, pgis_cfg_file)
          logging.get_logger().log('Analyzer::analyze_local: command finished', level='debug')
        else:
          tracker.m_track('Initial analysis', util, 'run_analyser_command_noInstr', command, analyzer_dir, flavor,
                          benchmark_name)
          util.run_analyser_command_noInstr(command, analyzer_dir, flavor, benchmark_name)

        self.tear_down(build, exp_dir)
        return instr_files

      except Exception as e:
        logging.get_logger().log(str(e), level='error')

        raise Exception('Problem in Analyzer')

  def analyse_slurm(self, flavors, build, benchmark, kwargs, config):
    logging.get_logger().log('Analyzer::analyse_slurm: Not implemented. Aborting.', level='error')
    assert(False)

  def set_up(self):
    pass

  def tear_down(self, old_dir, exp_dir):
    isdirectory_good = util.check_provided_directory(exp_dir)
    if isdirectory_good:
      try:
        util.change_cwd(old_dir)
      except Exception as e:
        logging.get_logger().log(str(e), level='error')

  def analyze(self, target_config, iteration_number: int) -> str:
    default_provider = defaults.BackendDefaults()
    kwargs = default_provider.get_default_kwargs()

    flavor = target_config.get_flavor()
    build = target_config.get_build()
    benchmark = target_config.get_target()
    return self.analyze_local(flavor, build, benchmark, kwargs, iteration_number)
