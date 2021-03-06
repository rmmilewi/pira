"""
File: Pira.py
License: Part of the PIRA project. Licensed under BSD 3 clause license. See LICENSE.txt file at https://github.com/jplehr/pira/LICENSE.txt
Description: Module implementing the main workflow of PIRA.
"""

from lib.ConfigurationLoader import SimplifiedConfigurationLoader as SCLoader
from lib.ConfigurationLoader import ConfigurationLoader as CLoader
from lib.Configuration import TargetConfiguration, PiraConfiguration, ExtrapConfiguration, InvocationConfiguration, PiraConfigurationErrorException
from lib.Runner import Runner, LocalRunner, LocalScalingRunner
from lib.Builder import Builder as B
from lib.Analyzer import Analyzer as A
from lib.Checker import Checker as checker
import lib.Logging as log
import lib.Utility as util
import lib.BatchSystemHelper as bat_sys
import lib.FunctorManagement as fm
import lib.Measurement as ms
import lib.TimeTracking as tt
import lib.Database as d
import lib.ProfileSink as sinks
from lib.RunnerFactory import PiraRunnerFactory

import typing
import sys


def execute_with_config(runner: Runner, analyzer: A, pira_iters: int, target_config: TargetConfiguration) -> None:
  try:
    log.get_logger().log('run_setup phase.', level='debug')
    instrument = False
    pira_iterations = pira_iters 

    # Build without any instrumentation
    vanilla_builder = B(target_config, instrument)
    tracker = tt.TimeTracker()
    tracker.m_track('Vanilla Build', vanilla_builder, 'build')

    # Run without instrumentation for baseline
    log.get_logger().log('Running baseline measurements', level='info')
    vanilla_rr = runner.do_baseline_run(target_config)
    log.get_logger().log(
        'Pira::execute_with_config: RunResult: ' + str(vanilla_rr) + ' | avg: ' + str(vanilla_rr.get_average()),
        level='debug')
    instr_file = ''

    for x in range(0, pira_iterations):
      log.get_logger().log('Running instrumentation iteration ' + str(x), level='info')

      # Only run the pgoe to get the functions name
      iteration_tracker = tt.TimeTracker()

      # Analysis Phase
      instr_file = analyzer.analyze(target_config, x)
      log.get_logger().log('[WHITELIST] $' + str(x) + '$ ' + str(util.lines_in_file(instr_file)), level='perf')
      util.shell('stat ' + instr_file)

      # After baseline measurement is complete, do the instrumented build/run
      # This is only necessary in every iteration when run in compile-time mode.
      if x is 0 or target_config.is_compile_time_filtering():
        instrument = True
        instr_builder = B(target_config, instrument, instr_file)
        tracker.m_track('Instrument Build', instr_builder, 'build')

      #Run Phase
      log.get_logger().log('Running profiling measurements', level='info')
      instr_rr = runner.do_profile_run(target_config, x)

      # Compute overhead of instrumentation
      ovh_percentage = instr_rr.compute_overhead(vanilla_rr)
      log.get_logger().log('[RUNTIME] $' + str(x) + '$ ' + str(instr_rr.get_average()), level='perf')
      log.get_logger().log('[OVERHEAD] $' + str(x) + '$ ' + str(ovh_percentage), level='perf')

      iteration_tracker.stop()
      user_time, system_time = iteration_tracker.get_time()
      log.get_logger().log('[ITERTIME] $' + str(x) + '$ ' + str(user_time) + ', ' + str(system_time), level='perf')

  except Exception as e:
    log.get_logger().log(
        'Pira::execute_with_config: Problem during preparation of run.\nMessage:\n' + str(e), level='error')
    raise RuntimeError(str(e))


def process_args_for_extrap(cmdline_args) -> typing.Tuple[bool, str]:
  use_extra_p = False
  extrap_config = ExtrapConfiguration('', '', '')
  if cmdline_args.extrap_dir is not '':
    use_extra_p = True
    extrap_config = ExtrapConfiguration(cmdline_args.extrap_dir, cmdline_args.extrap_prefix, '')

    num_reps = cmdline_args.repetitions
    if num_reps < 5:
      log.get_logger().log('At least 5 repetitions are recommended for Extra-P modelling.', level='warn')
      if num_reps < 0:
        log.get_logger().log('REMEMBER TO REMOVE IN PIRA::process_args_for_extrap', level='warn')
        log.get_logger().log('At least 3 repetitions are required for Extra-P modelling.', level='error')
        raise RuntimeError('At least 5 repetitions are needed for Extra-P modelling.')

  return use_extra_p, extrap_config


def show_pira_invoc_info(cmdline_args) -> None:
  invoc_cfg = process_args_for_invoc(cmdline_args)
  cf_str = 'compile-time filtering'
  if not invoc_cfg.is_compile_time_filtering():
    cf_str = 'runtime filtering'
  log.get_logger().log(
      'Pira::main: Running PIRA in ' + cf_str + ' with configuration\n ' + str(invoc_cfg.get_path_to_cfg()),
      level='info')


def process_args_for_invoc(cmdline_args) -> None:
  path_to_config = cmdline_args.config
  compile_time_filter = not cmdline_args.runtime_filter
  pira_iters = cmdline_args.iterations
  num_reps = cmdline_args.repetitions

  invoc_cfg = InvocationConfiguration(path_to_config, compile_time_filter, pira_iters, num_reps)

  return invoc_cfg


def main(arguments) -> None:
  """ Main function for pira framework. Used to invoke the various components. """
  show_pira_invoc_info(arguments)

  invoc_cfg = process_args_for_invoc(arguments)
  use_extra_p, extrap_config = process_args_for_extrap(arguments)

  home_dir = util.get_cwd()
  util.set_home_dir(home_dir)

  try:
    if arguments.version is 1:
      config_loader = CLoader()
      configuration = config_loader.load_conf(invoc_cfg.get_path_to_cfg())
      checker.check_configfile_v1(configuration)
    else:
      config_loader = SCLoader()
      configuration = config_loader.load_conf(invoc_cfg.get_path_to_cfg())
      checker.check_configfile_v2(configuration)

    if bat_sys.check_queued_job():
      # FIXME: Implement
      log.get_logger().log('In this version of PIRA it is not yet implemented', level='error')
      assert (False)

    else:
      '''
      This branch is running PIRA actively on the local machine.
      It is blocking, and the user can track the progress in the terminal.
      '''
      log.get_logger().log('Running the local case')

      # The FunctorManager manages loaded functors and generates the respective names
      fm.FunctorManager(configuration)
      dbm = d.DBManager(d.DBManager.db_name + '.' + d.DBManager.db_ext)
      dbm.create_cursor()
      analyzer = A(configuration)

      runner_factory = PiraRunnerFactory(invoc_cfg, configuration)
      runner = runner_factory.get_simple_local_runner()
      if use_extra_p:
        log.get_logger().log('Running with Extra-P runner')
        runner = runner_factory.get_scalability_runner(extrap_config)

      if runner.has_sink():
        analyzer.set_profile_sink(runner.get_sink())

      # A build/place is a top-level directory
      for build in configuration.get_builds():
        log.get_logger().log('Build: ' + str(build))
        app_tuple = (util.generate_random_string(), build, '', '')
        dbm.insert_data_application(app_tuple)

        # An item is a target/software in that directory
        for item in configuration.get_items(build):
          log.get_logger().log('Running for item ' + str(item))

          # A flavor is a specific version to build
          if configuration.has_local_flavors(build, item):
            for flavor in configuration.get_flavors(build, item):
              log.get_logger().log('Running for local flavor ' + flavor, level='debug')

              # prepare database, and get a unique handle for current item.
              db_item_id = dbm.prep_db_for_build_item_in_flavor(configuration, build, item, flavor)
              # Create configuration object for the item currently processed.
              place = configuration.get_place(build)
              t_config = TargetConfiguration(place, build, item, flavor, db_item_id, invoc_cfg.is_compile_time_filtering())

              # Execute using a local runner, given the generated target description
              execute_with_config(runner, analyzer, invoc_cfg.get_pira_iters(), t_config)

          # If global flavor
          else:
            # TODO: Implement
            log.get_logger().log('In this version of PIRA it is not yet implemented', level='error')
            assert (False)

    util.change_cwd(home_dir)

  except RuntimeError as rt_err:
    util.change_cwd(home_dir)
    log.get_logger().log('Runner.run caught exception. Message: ' + str(rt_err), level='error')
    log.get_logger().dump_tape()
    sys.exit(-1)
