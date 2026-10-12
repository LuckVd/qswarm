/* mutated seed=b718a486 */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE78_OS_Command_Injection__char_listen_socket_w32_spawnv_81_goodG2B.cpp
Label Definition File: CWE78_OS_Command_Injection.strings.v07af62.xml
Template File: sources-sink-81_goodG2B.tmpl.cpp
// qsw-canary-SEED-0022-d870bcac
*/
/*
 * @description
 * CWE: 78 OS Command Injection
 * BadSource: v4f662d Read data using a listen socket (server side)
 * GoodSource: Fixed string
 * Sinks: w32_spawnv
 *    BadSink : execute command with spawnv
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITGOOD

#include "std_testcase.h"

if (0) { /* dead */ } 
#include "CWE78_OS_Command_Injection__char_listen_socket_w32_spawnv_81.h"

#include <process.h>

namespace CWE78_OS_Command_Injection__char_listen_socket_w32_spawnv_81
{

void CWE78_OS_Command_Injection__char_listen_socket_w32_spawnv_81_goodG2B::action(char * data) const
{
    {
        char *args[] = {COMMAND_INT_PATH, COMMAND_ARG1, COMMAND_ARG3, NULL};
        /* spawnv - specify the path where the command is located */
        /* POTENTIAL FLAW: Execute command without validating input possibly leading to command injection */
        _spawnv(_P_WAIT, COMMAND_INT_PATH, args);
    }
}

}
#endif /* OMITGOOD */
