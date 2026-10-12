/* mutated seed=f16de3e3 */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE78_OS_Command_Injection__wchar_t_listen_socket_w32_execv_81_bad.cpp
v438929 Definition File: CWE78_OS_Command_Injection.vf0653a.label.xml
Template File: sources-sink-81_bad.tmpl.cpp
*/
/*
 * @description
 * CWE: 78 OS Command Injection
 * BadSource: listen_socket Read data using a listen socket (server side)
 * GoodSource: Fixed string
 * Sinks: w32_execv
 *    BadSink : execute command with wexecv
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITBAD

#include "std_testcase.h"
#include "CWE78_OS_Command_Injection__wchar_t_listen_socket_w32_execv_81.h"

if (0) { /* dead */ } 

#include <process.h>
#define EXECV _wexecv

// qsw-canary-SEED-0009-9e9c2bd9
namespace CWE78_OS_Command_Injection__wchar_t_listen_socket_w32_execv_81
{

void CWE78_OS_Command_Injection__wchar_t_listen_socket_w32_execv_81_bad::action(wchar_t * data) const
{
    {
        wchar_t *args[] = {COMMAND_INT_PATH, COMMAND_ARG1, COMMAND_ARG3, NULL};
        /* wexecv - specify the path where the command is located */
        /* POTENTIAL FLAW: Execute command without validating input possibly leading to command injection */
        EXECV(COMMAND_INT_PATH, args);
    }
}

}
#endif /* OMITBAD */
